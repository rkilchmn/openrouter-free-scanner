# OpenRouter Free Scanner

A simple command-line tool and Python library to fetch, filter, and save a list of free models from OpenRouter.ai.

## Installation

```bash
pip install .
```

## Command-Line Usage

To display the list of free models in the console:

```bash
openrouter-free-scanner
```

To save the list of free models to a JSON file named `free_models.json`:

```bash
openrouter-free-scanner -o
```

### Advanced Usage

- `--limit <N>`: Limit the number of models returned.
- `--name <string>`: Filter models by name.
- `--min-context-length <N>`: Filter by minimum context length.
- `--provider <string>`: Filter by provider.
- `--sort-by <field>`: Sort models by a specific field (e.g., `name`, `context_length`).
- `--reverse`: Reverse the sort order.

**Example:** Get the top 5 free models with the longest context length:

```bash
openrouter-free-scanner --limit 5 --sort-by context_length --reverse
```

## Programmatic Usage

You can also use this package as a library in your Python code:

```python
import openrouterfreescanner

# Get only free models
free_models = openrouterfreescanner.get_free_models()

# Get free models, including routers
free_models_with_routers = openrouterfreescanner.get_free_models(exclude_routers=False)

# Filter free models by name
gemma_models = openrouterfreescanner.filter_models(free_models, name="gemma")

# Filter free models by provider and context length
filtered_models = openrouterfreescanner.filter_models(free_models, provider="google", min_context_length=8000)

# Sort free models by context length in descending order
sorted_models = openrouterfreescanner.sort_models(free_models, sort_by="context_length", reverse=True)
```