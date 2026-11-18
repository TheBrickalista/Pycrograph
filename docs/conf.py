# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------

project = "Pycrograph"
copyright = "2026, Etienne Boulter"
author = "Etienne Boulter"
release = "0.1.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",  # lets Sphinx read Markdown (.md) as well as reStructuredText
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- HTML output -------------------------------------------------------------

html_theme = "furo"
html_title = "Pycrograph"
html_static_path = ["_static"]
