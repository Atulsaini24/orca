# The dev container image for GitHub Codespaces (devcontainer.json). The base image lists Yarn's apt repository,
# whose signing key has expired: every `apt-get update` then fails, and with it the Node, Git LFS and SSH features,
# which left Codespaces in a bare recovery container. ORCA needs nothing from Yarn's repository.
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm
RUN rm -f /etc/apt/sources.list.d/yarn*
