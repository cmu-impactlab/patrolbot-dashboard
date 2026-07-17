from setuptools import setup

package_name = "patrolbot_web_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", ["config/web_bridge.yaml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Yousef Hussein",
    maintainer_email="ymh1874@gmail.com",
    description="Telemetry bridge from the PatrolBot ROS 2 graph to the dashboard server",
    license="MIT",
    entry_points={
        "console_scripts": [
            "bridge_node = patrolbot_web_bridge.bridge_node:main",
        ],
    },
)
