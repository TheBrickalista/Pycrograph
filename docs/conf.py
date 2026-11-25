# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------

project = "Pycrograph"
copyright = "2026, Etienne Boulter and Chloé C. Féral"
author = "Etienne Boulter and Chloé C. Féral"
release = "0.2.2"

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
# The PG mark above the project name in the sidebar (the full logo repeats the
# name in its wordmark); the same mark as favicon.
html_logo = "_static/logo/pycrograph_icon.svg"
html_favicon = "_static/logo/pycrograph_icon.svg"
