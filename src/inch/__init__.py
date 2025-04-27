"""
Inch: A library for tracking progress of computational tasks.

This module provides tools for creating and executing tasks with
progress tracking capabilities.
"""

import logging

# Import classes from their respective modules
from inch.core import Inch
from inch.executor import InchPoolExecutor
from inch.func_inch import FuncInch

# Set up logging for the inch module
logging.getLogger("inch").addHandler(logging.NullHandler())

# Define what's available when importing from inch
__all__ = ["FuncInch", "Inch", "InchPoolExecutor"]

# Version information
__version__ = "0.1.0"
