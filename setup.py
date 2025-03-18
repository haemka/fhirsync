from setuptools import setup, find_packages


setup(
    name='fhir-sync',
    version='0.5.0',
    packages=find_packages(),
    install_requires=[
        'fhir.resources',
        'dic-helper-toolkit'
    ],
    entry_points={
        'console_scripts': [
            'fhirsync=sync:run',
        ],
    },
)
