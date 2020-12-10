FROM ubuntu:18.04

ENV PYTHONUNBUFFERED 1
ENV TZ America/New_York
ENV DEBIAN_FRONTEND noninteractive

# fix for: apt fails to install some packages. the problem is stale cached package repositories  --fix-missing
RUN apt-get clean && apt-get update && apt install -y wget apt-utils sudo python3 git python3-pip vim net-tools

# make a non-root user for olympe (required), but make it possible to use sudo inside the container
RUN groupadd --gid 5000 olympe && useradd --home-dir /home/olympe --create-home --uid 5000 --gid 5000 --shell /bin/sh --skel /dev/null olympe && \
    adduser olympe sudo && echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers 

# download google repo (git meta tool)
RUN wget https://storage.googleapis.com/git-repo-downloads/repo -O /usr/bin/repo && chmod +x /usr/bin/repo

# fix for: postinst requires system python to be "python" not "python3"
RUN ln -s /usr/bin/python3 /usr/bin/python

# parrot-groundsdk is picky about c compiler. This works for now
RUN python3 -m pip install clang==6.0.0.2

# now try to do everything as non-root to avoid file ownership permission problems later

# EULA can't be automatically accepted with a command line argument, so delete the file to make postinst work. Using this docker image implicitely accepts the EULA
USER olympe
RUN git config --global user.email "olympe@olympe.olympe" && git config --global user.name "olympe" && \
    cd /home/olympe/ && \
    touch .bashrc && \
    mkdir -p code/parrot-groundsdk && \
    cd code/parrot-groundsdk && \
    repo init -u https://github.com/Parrot-Developers/groundsdk-manifest.git && \
    repo sync && \
    rm .repo/manifests/EULA.md 

# postinst must run as root
USER root
ENV TZ America/New_York
ENV DEBIAN_FRONTEND noninteractive
RUN /home/olympe/code/parrot-groundsdk/products/olympe/linux/env/postinst

# must be non-root user for build.sh
USER olympe
RUN cd /home/olympe/code/parrot-groundsdk && ./build.sh -p olympe-linux -A all final -j

WORKDIR /home/olympe/code/parrot-groundsdk
SHELL ["/bin/bash", "-c"]

# I think it's really annoying that postinst runs as root but then makes the python sitelib files in ~olympe owned by root
# because then olympe can't run pip to install more packages, but sudo pip also fails because it uses the system sitelib.
# fix: restore ownership to olympe
RUN sudo chown -R olympe:olympe /home/olympe/code/parrot-groundsdk/.python

# bake the ENV variables into the container so sourcing the shell script isn't required to run the container
ENV COMMON_DIR=/home/olympe/code/parrot-groundsdk/products/olympe/linux \
ENV_DIR=/home/olympe/code/parrot-groundsdk/products/olympe/linux/env \
LD_LIBRARY_PATH=/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/lib:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib: \
LIBRARY_PATH=/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/lib:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib: \
MODULES=olympe \
MODULES_DIR=/home/olympe/code/parrot-groundsdk/packages \
OLYMPE_GENERATE=/home/olympe/code/parrot-groundsdk/packages/olympe/src/olympe/ \
OLYMPE_LIB_PATH=/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib \
OLYMPE_XML=/home/olympe/code/parrot-groundsdk/out/olympe-linux/staging-host/usr/lib/arsdkgen/xml \
PATH=/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/bin:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/bin:/home/olympe/code/parrot-groundsdk/.python//py3/bin:/home/olympe/code/parrot-groundsdk/.python/py3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
PRODUCT_DIR=/home/olympe/code/parrot-groundsdk/products \
PYTHONPATH=/home/olympe/code/parrot-groundsdk/packages/olympe/src:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib/python:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib/python/site-packages:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/local/lib/python:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/local/lib/python/site-packages:/home/olympe/code/parrot-groundsdk/out/olympe-linux/staging-host/usr/lib/arsdkgen \
PYTHON_ENV_DIR=/home/olympe/code/parrot-groundsdk/.python//py3 \
ROOT_DIR=/home/olympe/code/parrot-groundsdk \
SYSROOT=/home/olympe/code/parrot-groundsdk/out/olympe-linux/final \
VIRTUAL_ENV=/home/olympe/code/parrot-groundsdk/.python/py3 \
sourced=1 \
m=olympe 

# finally install package(s) I need for my project
RUN which pip3 && pip3 --version && pip3 install pyzmq protobuf piexif

# fix very annoying bug - olympe websockets don't work due to a missing constant
# diff -u packages/olympe/src/olympe/media.py media.py  >patch-media.patch
USER root
COPY patch-media.patch /home/olympe/code/parrot-groundsdk
RUN patch -u packages/olympe/src/olympe/media.py -i patch-media.patch


