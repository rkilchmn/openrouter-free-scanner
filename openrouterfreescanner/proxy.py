import argparse
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from .main import get_free_models, filter_models, sort_models


class ModelStats:
    """Track error statistics for each model."""
    
    def __init__(self, error_threshold=3):
        self.stats = {}  # model_id -> {'errors': count, 'successes': count, 'last_error': timestamp}
        self.error_threshold = error_threshold
    
    def record_error(self, model_id):
        """Record an error for a model."""
        if model_id not in self.stats:
            self.stats[model_id] = {'errors': 0, 'successes': 0, 'last_error': None}
        
        self.stats[model_id]['errors'] += 1
        self.stats[model_id]['last_error'] = time.time()
    
    def record_success(self, model_id):
        """Record a successful request for a model."""
        if model_id not in self.stats:
            self.stats[model_id] = {'errors': 0, 'successes': 0, 'last_error': None}
        
        self.stats[model_id]['successes'] += 1
    
    def is_model_available(self, model_id):
        """Check if a model is available based on error threshold."""
        if model_id not in self.stats:
            return True
        
        stats = self.stats[model_id]
        
        # If last error was more than 5 minutes ago, reset error count
        if stats['last_error'] and (time.time() - stats['last_error']) > 300:
            stats['errors'] = 0
            return True
        
        return stats['errors'] < self.error_threshold
    
    def get_best_model(self, models):
        """Get the best model based on success rate and availability."""
        available_models = [m for m in models if self.is_model_available(m['id'])]
        
        if not available_models:
            # Reset all stats if no models are available
            self.stats = {}
            return models[0] if models else None
        
        # Sort by success rate (successes / (successes + errors))
        def success_rate(model):
            model_id = model['id']
            if model_id not in self.stats:
                return 1.0  # New models get priority
            
            stats = self.stats[model_id]
            total = stats['successes'] + stats['errors']
            if total == 0:
                return 1.0
            
            return stats['successes'] / total
        
        return max(available_models, key=success_rate)


class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OpenAI-compatible proxy."""
    
    # Class variables shared across all instances
    models_list = []
    model_stats = None
    api_key = None
    
    def log_message(self, format, *args):
        """Override to add timestamp to logs."""
        print(f"[{self.log_date_time_string()}] {format % args}")
    
    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/v1/models':
            self.handle_list_models()
        elif parsed_path.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self.send_error(404, "Endpoint not found")
    
    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/v1/chat/completions':
            self.handle_chat_completion()
        else:
            self.send_error(404, "Endpoint not found")
    
    def handle_list_models(self):
        """Handle /v1/models endpoint - OpenAI compatible."""
        try:
            models_data = {
                'object': 'list',
                'data': [
                    {
                        'id': model['id'],
                        'object': 'model',
                        'created': int(time.time()),
                        'owned_by': 'openrouter',
                        'permission': [],
                        'root': model['id'],
                        'parent': None,
                    }
                    for model in self.models_list
                ]
            }
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(models_data, indent=2).encode())
        
        except Exception as e:
            self.send_error(500, f"Internal error: {str(e)}")
    
    def handle_chat_completion(self):
        """Handle /v1/chat/completions endpoint - OpenAI compatible with failover."""
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            request_data = json.loads(body.decode('utf-8'))
            
            # Extract API key from Authorization header
            auth_header = self.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                self.send_error(401, "Missing or invalid Authorization header")
                return
            
            client_api_key = auth_header.replace('Bearer ', '').strip()
            
            # Get the best available model
            best_model = self.model_stats.get_best_model(self.models_list)
            
            if not best_model:
                self.send_error(503, "No models available")
                return
            
            # Override model in request with our selected model
            original_model = request_data.get('model', 'unknown')
            request_data['model'] = best_model['id']
            
            self.log_message(f"Routing request (original model: {original_model}) to: {best_model['id']}")
            
            # Try to make request to OpenRouter
            max_retries = min(3, len(self.models_list))
            
            for attempt in range(max_retries):
                try:
                    response = requests.post(
                        'https://openrouter.ai/api/v1/chat/completions',
                        headers={
                            'Authorization': f'Bearer {client_api_key}',
                            'Content-Type': 'application/json',
                            'HTTP-Referer': 'https://github.com/tcsenpai/openrouter-free-scanner',
                            'X-Title': 'OpenRouter Free Proxy'
                        },
                        json=request_data,
                        timeout=60
                    )
                    
                    # Check for rate limit errors
                    if response.status_code == 429 or (response.status_code >= 400 and 'rate' in response.text.lower()):
                        self.log_message(f"Rate limit hit for {best_model['id']}: {response.text}")
                        self.model_stats.record_error(best_model['id'])
                        
                        # Try next model
                        if attempt < max_retries - 1:
                            best_model = self.model_stats.get_best_model(self.models_list)
                            if best_model:
                                request_data['model'] = best_model['id']
                                self.log_message(f"Retrying with next model: {best_model['id']}")
                                continue
                        
                        # No more models to try
                        self.send_response(response.status_code)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(response.content)
                        return
                    
                    # Success!
                    if response.status_code == 200:
                        self.model_stats.record_success(best_model['id'])
                        self.log_message(f"Success with model: {best_model['id']}")
                    
                    # Forward response to client
                    self.send_response(response.status_code)
                    for header, value in response.headers.items():
                        if header.lower() not in ['transfer-encoding', 'connection']:
                            self.send_header(header, value)
                    self.end_headers()
                    self.wfile.write(response.content)
                    return
                
                except requests.exceptions.RequestException as e:
                    self.log_message(f"Request error for {best_model['id']}: {str(e)}")
                    self.model_stats.record_error(best_model['id'])
                    
                    if attempt < max_retries - 1:
                        best_model = self.model_stats.get_best_model(self.models_list)
                        if best_model:
                            request_data['model'] = best_model['id']
                            continue
                    
                    self.send_error(502, f"Bad Gateway: {str(e)}")
                    return
        
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON in request body")
        except Exception as e:
            self.log_message(f"Unexpected error: {str(e)}")
            self.send_error(500, f"Internal error: {str(e)}")


def start_proxy_server(port, limit=None, name=None, min_context_length=None, 
                       provider=None, sort_by='context_length', reverse=True,
                       error_threshold=3):
    """
    Start the OpenAI-compatible proxy server.
    
    Args:
        port (int): Port to run the server on
        limit (int): Limit number of models
        name (str): Filter models by name
        min_context_length (int): Minimum context length
        provider (str): Filter by provider
        sort_by (str): Field to sort by
        reverse (bool): Reverse sort order
        error_threshold (int): Number of errors before switching models
    """
    print("Fetching free models from OpenRouter...")
    models = get_free_models()
    
    if not models:
        print("Error: Could not fetch models from OpenRouter")
        return
    
    # Apply filters
    models = filter_models(models, name=name, min_context_length=min_context_length, provider=provider)
    models = sort_models(models, sort_by=sort_by, reverse=reverse)
    
    if limit:
        models = models[:limit]
    
    if not models:
        print("Error: No models match the specified criteria")
        return
    
    print(f"\nLoaded {len(models)} free models:")
    for i, model in enumerate(models[:10], 1):
        print(f"  {i}. {model['id']} (context: {model.get('context_length', 'N/A')})")
    if len(models) > 10:
        print(f"  ... and {len(models) - 10} more")
    
    # Initialize shared state
    ProxyHandler.models_list = models
    ProxyHandler.model_stats = ModelStats(error_threshold=error_threshold)
    
    # Start server
    server = HTTPServer(('0.0.0.0', port), ProxyHandler)
    
    print(f"\n{'='*60}")
    print(f"OpenRouter Free Proxy Server running on http://0.0.0.0:{port}")
    print(f"{'='*60}")
    print(f"\nOpenAI-compatible endpoints:")
    print(f"  - GET  http://localhost:{port}/v1/models")
    print(f"  - POST http://localhost:{port}/v1/chat/completions")
    print(f"  - GET  http://localhost:{port}/health")
    print(f"\nExample usage with OpenAI Python client:")
    print(f"  from openai import OpenAI")
    print(f"  client = OpenAI(")
    print(f"    base_url='http://localhost:{port}/v1',")
    print(f"    api_key='your-openrouter-api-key'")
    print(f"  )")
    print(f"\nPress Ctrl+C to stop the server\n")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        server.shutdown()
        print("Server stopped.")


def main():
    """Main entry point for the proxy CLI."""
    parser = argparse.ArgumentParser(
        description="Start an OpenAI-compatible proxy server for OpenRouter free models with automatic failover."
    )
    parser.add_argument("--port", type=int, default=8080, help="Port to run the server on (default: 8080)")
    parser.add_argument("--limit", type=int, help="Limit the number of models to use")
    parser.add_argument("--name", type=str, help="Filter models by name")
    parser.add_argument("--min-context-length", type=int, help="Filter by minimum context length")
    parser.add_argument("--provider", type=str, help="Filter by provider")
    parser.add_argument("--sort-by", type=str, default="context_length", 
                       help="Sort models by field (default: context_length)")
    parser.add_argument("--reverse", action="store_true", default=True,
                       help="Reverse sort order (default: True)")
    parser.add_argument("--error-threshold", type=int, default=3,
                       help="Number of errors before switching models (default: 3)")
    
    args = parser.parse_args()
    
    start_proxy_server(
        port=args.port,
        limit=args.limit,
        name=args.name,
        min_context_length=args.min_context_length,
        provider=args.provider,
        sort_by=args.sort_by,
        reverse=args.reverse,
        error_threshold=args.error_threshold
    )


if __name__ == "__main__":
    main()