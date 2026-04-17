from setuptools import find_packages, setup


setup(
    name="infra-pilot",
    version="0.1.0",
    description="Multi-cloud CLI automation and inventory tooling for AWS, Azure, and GCP.",
    author="Project Contributors",
    author_email="maintainers@example.invalid",
    license="MIT",
    url="https://example.com/infra-pilot",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "infrapilot=infra_pilot.cli:main",
        ]
    },
)
