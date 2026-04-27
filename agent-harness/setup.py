"""Package configuration for cli-anything-memory-web.

Uses PEP 420 namespace packages so multiple cli-anything CLIs can coexist
in the same Python environment under the shared `cli_anything` namespace.

Install:
    pip install -e .

Verify:
    cli-anything-memory-web --help
"""

from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-memory-web",
    version="1.0.0",
    description="Agent-friendly CLI harness for the MemoryWeb personal AI memory system",
    long_description=open("cli_anything/memory_web/README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="FORGE / cli-anything",
    python_requires=">=3.10",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-memory-web=cli_anything.memory_web.memory_web_cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
