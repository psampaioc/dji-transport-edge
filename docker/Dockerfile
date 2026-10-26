# ROS 2 and the direct Android UDP/RTP receiver run inside this project image.
FROM ros:humble-ros-base

SHELL ["/bin/bash", "-c"]
ARG USERNAME=edge
ARG USER_UID=1000
ARG USER_GID=1000
ENV DEBIAN_FRONTEND=noninteractive \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    ROS_DOMAIN_ID=7

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      python3-colcon-common-extensions \
      python3-gi \
      python3-pyproj \
      gir1.2-gstreamer-1.0 \
      gir1.2-gst-plugins-base-1.0 \
      gstreamer1.0-tools \
      gstreamer1.0-plugins-base \
      libgstreamer1.0-dev \
      libgstreamer-plugins-base1.0-dev \
      gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad \
      gstreamer1.0-libav \
      gstreamer1.0-x \
      nlohmann-json3-dev \
      ros-humble-ament-index-cpp \
      ros-humble-pcl-conversions \
      ros-humble-pcl-ros \
      ros-humble-rmw-cyclonedds-cpp \
      ros-humble-rviz2 \
      ros-humble-tf2-ros \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}"

ENV HOME=/home/${USERNAME}
WORKDIR /workspace
USER ${USERNAME}

CMD ["bash"]
