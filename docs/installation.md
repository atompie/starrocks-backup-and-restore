# Installation Guide

Choose the installation method that best fits your needs.

## Option 1: Install from PyPI

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate    # Windows

# Install
pip install starrocks-br

# Verify
python -c "import starrocks_br.api.app"
```

**Note:** Always activate the virtual environment before using the tool. This installs FastAPI,
Uvicorn, SQLAlchemy, Alembic, httpx, croniter, cryptography, and boto3 as base dependencies — see
[API Server](api.md) for how to configure and run the server.

## Option 2: Micromamba

[Micromamba](https://mamba.readthedocs.io/en/latest/user_guide/micromamba.html) is a lightweight package manager that creates isolated environments. It's useful when you want environment isolation without a full Anaconda/Miniconda installation.

```bash
# Install micromamba (if not already installed)
"${SHELL}" <(curl -L micro.mamba.pm/install.sh)

# Create environment and install
micromamba create -n starrocks-br python=3.11 -c conda-forge
micromamba activate starrocks-br
pip install starrocks-br

# Verify
python -c "import starrocks_br.api.app"
```

**Running without activation:**

```bash
micromamba run -n starrocks-br uvicorn starrocks_br.api.app:create_app --factory
```

## Option 3: Devbox (Development)

**Recommended for contributors.**

```bash
# Clone the repository
git clone https://github.com/deep-bi/starrocks-backup-and-restore
cd starrocks-br

# Install devbox (if not already installed)
curl -fsSL https://get.jetpack.io/devbox | bash

# Start devbox shell (auto-installs everything)
devbox shell

# Ready to go
pytest
```

## Option 4: Manual Development Setup

```bash
# Clone the repository
git clone https://github.com/deep-bi/starrocks-backup-and-restore
cd starrocks-br

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac

# Install in editable mode
pip install -e ".[dev]"

# Verify
pytest
```

## Next Steps

- **New users**: See [Getting Started](getting-started.md)
- **Configuration**: Check [Configuration Reference](configuration.md)
