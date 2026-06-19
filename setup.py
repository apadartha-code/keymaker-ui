import os
import re
from setuptools import setup, find_packages

def parse_toml_dependencies():
    """Safely extracts dependencies from the pyproject.toml array."""
    dependencies = []
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml", "r", encoding="utf-8") as f:
            content = f.read()
            match = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if match:
                inside_brackets = match.group(1)
                for line in inside_brackets.splitlines():
                    line = line.split('#')[0].strip()
                    dep = line.replace('"', '').replace("'", "").replace(",", "").strip()
                    if dep:
                        dependencies.append(dep)
    return dependencies

setup(
    name="keymaker_ui",
    version="0.1.0",
    description="An interactive image verification blueprint widget for Flask applications.",
    long_description=open("README.md", encoding="utf-8").read() if os.path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    python_requires=">=3.8",  # Lowered to 3.8 to allow your local install
    classifiers=[
        "Framework :: Flask",
        "Programming Language :: Python :: 3",
    ],
    
    # Matches your [tool.setuptools.packages.find]
    package_dir={"": "src"},
    packages=find_packages(where="src", include=["keymaker_ui*"]),
    
    # Matches your [tool.setuptools.package-data]
    package_data={
        "keymaker_ui": ["templates/*.html", "static/**/*"],
    },
    include_package_data=True,
    
    install_requires=parse_toml_dependencies(),
    zip_safe=False,
)