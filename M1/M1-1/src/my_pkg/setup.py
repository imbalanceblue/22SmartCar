import os
from glob import glob

from setuptools import setup

package_name = "my_pkg"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        # M1-2:launch 文件与参数文件必须装进 share 目录,
        # ros2 launch / 默认参数路径才能找到它们
        (os.path.join("share", package_name, "launch"),
         glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"),
         glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="imbalanceblue",
    maintainer_email="imbalanceblue@example.com",
    description="M1-1 三节点链路:传感器 -> 滤波 -> 报警",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "sensor_node = my_pkg.sensor_node:main",
            "filter_node = my_pkg.filter_node:main",
            "executor_node = my_pkg.executor_node:main",
        ],
    },
)
