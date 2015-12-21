from setuptools import setup


def read(path):
    """
    Read the contents of a file.
    """
    with open(path) as f:
        return f.read()


setup(
    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: Implementation :: CPython',
    ],
    name='benchmark-server',
    description="Persist benchmarking results",
    install_requires=[
        "klein"
    ],
    extras_require={},
    entry_points={},
    keywords="",
    license="Apache 2.0",
    url="https://github.com/ClusterHQ/benchmark-server/",
    maintainer='Bridget McErlean',
    maintainer_email='bridget.mcerlean@clusterhq.com',
    long_description=read('README.rst'),
)
