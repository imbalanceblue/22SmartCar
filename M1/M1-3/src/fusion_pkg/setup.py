import os
from glob import glob

from setuptools import setup

package_name = "fusion_pkg"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"),
         glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"),
         glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="imbalanceblue",
    maintainer_email="imbalanceblue@example.com",
    description="M1-3 激光与相机融合",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "static_tf = fusion_pkg.static_tf:main",
            "baggen = fusion_pkg.baggen:main",
            "projector = fusion_pkg.projector:main",
        ],
    },
)
