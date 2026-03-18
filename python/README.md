# OpenIDE Python Application

This project implements OpenIDE, an IDE for FPGA engineers to visually connect VHDL modules. It is modular, uses vector-based drawing, provides project and designer menus, logs actions, and saves text-based files for Git compatibility.

## Features
- Add, remove, and configure VHDL modules
- Visual wiring of modules with signals
- Vector-based graphics for performance
- Modular code architecture
- Logging and debugging
- Extensible menus
- Git-friendly file formats

## Structure
- `main.py`: Application entry point
- `ui/`: User interface components
- `designer/`: Structural design logic
- `modules/`: VHDL module management
- `signals/`: Wiring and signal handling
- `logging/`: Logging utilities
- `data/`: File handling and project management

## Getting Started
1. Install Python 3.8+
2. Run `python main.py`

## Future Work
- Expand menu features
- Improve module library
- Enhance vector drawing performance

## License
MIT License
