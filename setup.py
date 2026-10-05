from setuptools import setup, find_packages

setup(
    name="agent-chat-search",
    version="1.1.0",
    author="Tony Acero",
    description="Unified search across Hermes, Google Antigravity, and OpenClaw fleet chats",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "agent-search=agent_search.cli:main",
        ],
    },
    python_requires=">=3.8",
)
