from setuptools import setup, find_packages

setup(
    name="pyfelicita",
    version="0.1.0",
    description="A Bluetooth scale integration for coffee machines",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Kevin Guldager Stampe",
    author_email="kevinstampe@gmail.com",
    url="https://github.com/kevinstampe/pyfelicita",  # Link to your GitHub repo
    license="MIT",
    packages=find_packages(),  # Automatically find submodules
    install_requires=[
        "bleak"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",  # Adjust as needed
)