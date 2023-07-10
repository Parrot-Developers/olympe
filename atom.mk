LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe-base
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := Olympe pure python module
LOCAL_DEPENDS_MODULES := python arsdkparser parrot-protobuf-extensions-proto protobuf-base
LOCAL_LIBRARIES := arsdkparser parrot-protobuf-extensions-proto

PRIVATE_OLYMPE_OUT_DIR=$(TARGET_OUT_STAGING)$(shell echo $${TARGET_DEPLOY_ROOT:-/usr})

olympe_source_files := \
    $(call all-files-under,src,.py) \
    $(call all-files-under,src,.rst) \
    $(call all-files-under,src,.yaml) \
    $(call all-files-under,src,.js) \
    $(call all-files-under,src,.css) \
    $(call all-files-under,src,.png) \
    src/olympe/.flake8

# Install files in python site-packages staging directory
LOCAL_COPY_FILES := \
	$(foreach __f,$(olympe_source_files), \
		$(__f):$(PRIVATE_OLYMPE_OUT_DIR)/lib/python/site-packages/$(strip $(patsubst src/%, %, $(__f))) \
	)

# Install .proto files in python site-packages/olympe_protobuf staging directory
PRIVATE_OLYMPE_PROTOBUF_SRC_DIRS := $(PRIVATE_OLYMPE_OUT_DIR)/share/protobuf:$(PRIVATE_OLYMPE_OUT_DIR)/lib/python/site-packages
PRIVATE_OLYMPE_PROTOBUF_DST_DIR := $(PRIVATE_OLYMPE_OUT_DIR)/lib/python/site-packages/olympe_protobuf
define LOCAL_CMD_POST_INSTALL
    while read -d ':' src_dir; do \
        protobuf_src_files=$$(find $$src_dir -type f -name '*.proto'); \
        protobuf_dst_files=$$(echo $$protobuf_src_files | xargs -I{} -d' ' bash -c "echo \"{}\" | \
            sed 's#\s*$$src_dir#$(PRIVATE_OLYMPE_PROTOBUF_DST_DIR)#g'"); \
        while read -ra src <&3 && read -ra dst <&4; do \
            echo "$$src is in $$dst"; \
            install -Dp -m0660 $$src $$dst; \
        done 3<<<"$$protobuf_src_files" 4<<<"$$protobuf_dst_files"; \
    done <<< $(PRIVATE_OLYMPE_PROTOBUF_SRC_DIRS):; \
    echo $$protobuf_files;
endef

include $(BUILD_CUSTOM)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := Drone controller python library based on ctypes bindings of libpdraw, libpdraw-gles2hud and arsdk-ng
LOCAL_DEPENDS_MODULES := python arsdkparser olympe-base olympe-deps logness

include $(BUILD_CUSTOM)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe-wheel
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := olympe wheel package builder
LOCAL_DEPENDS_MODULES := olympe
LOCAL_PYTHONPKG_NO_ABI := $(true)
LOCAL_MODULE_FILENAME := parrot-olympe.whl

include $(BUILD_PYTHON_WHEEL)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe-deps
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := \
	Python ctypes bindings for olympe dependencies (libpdraw, ....)

LOCAL_LIBRARIES := libulog-py protobuf-python parrot-protobuf-extensions-py

LOCAL_EXPAND_CUSTOM_VARIABLES := 1

OLYMPE_DEPS_LIBS_NAME := $\
	libpomp:libpdraw:libpdraw-gles2hud:libvideo-metadata:libvideo-defs:$\
	libarsdk:libarsdkctrl:libmedia-buffers:libmedia-buffers-memory:$\
	libmedia-buffers-memory-generic:libmp4:libmux

OLYMPE_DEPS_HEADERS := $\
	/usr/include/stdint.h:$\
	LIBPOMP_HEADERS:$\
	LIBPDRAW_HEADERS:$\
	LIBPDRAW_GLES2HUD_HEADERS:$\
	LIBVIDEOMETADATA_HEADERS:$\
	LIBVIDEODEFS_HEADERS:$\
	LIBARSDK_HEADERS:$\
	LIBARSDKCTRL_HEADERS:$\
	LIBMEDIABUFFERS_HEADERS:$\
	LIBMEDIABUFFERSMEMORY_HEADERS:$\
	LIBMEDIABUFFERSMEMORYGENERIC_HEADERS:$\
	LIBMP4_HEADERS:$\
	LIBMUX_HEADERS

OLYMPE_DEPS_LIBS_DIR := \
	$(TARGET_OUT_STAGING)$(shell echo $${TARGET_DEPLOY_ROOT:-/usr})/lib/
OLYMPE_DEPS_BIN_DIR := \
	$(TARGET_OUT_STAGING)$(shell echo $${TARGET_DEPLOY_ROOT:-/usr})/bin/

OLYMPE_DEPS_LIBS_PATH := $\
	$(OLYMPE_DEPS_LIBS_DIR)libpomp$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libpdraw$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libpdraw-gles2hud$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libvideo-metadata$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libvideo-defs$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libarsdk$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libarsdkctrl$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libmedia-buffers$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libmedia-buffers-memory$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libmedia-buffers-memory-generic$(TARGET_SHARED_LIB_SUFFIX):$\
	$(OLYMPE_DEPS_LIBS_DIR)libmp4$(TARGET_SHARED_LIB_SUFFIX):$\
        $(OLYMPE_DEPS_LIBS_DIR)libmux$(TARGET_SHARED_LIB_SUFFIX)

ifeq ($(CONFIG_ALCHEMY_BUILD_LIBMETADATATHERMAL),y)
OLYMPE_DEPS_LIBS_NAME := $(OLYMPE_DEPS_LIBS_NAME):libmetadatathermal
OLYMPE_DEPS_HEADERS := $(OLYMPE_DEPS_HEADERS):LIBMETADATATHERMAL_HEADERS
OLYMPE_DEPS_LIBS_PATH := $(OLYMPE_DEPS_LIBS_PATH):$\
	$(OLYMPE_DEPS_LIBS_DIR)libmetadatathermal$(TARGET_SHARED_LIB_SUFFIX)
endif

LOCAL_CUSTOM_MACROS := $\
	pybinding-macro:olympe_deps,$\
	$(OLYMPE_DEPS_LIBS_NAME),$\
	$(OLYMPE_DEPS_HEADERS),$\
	$(OLYMPE_DEPS_LIBS_PATH)

LOCAL_DESTDIR := usr/lib/python/site-packages
LOCAL_LIBRARIES += libpomp libpdraw libpdraw-gles2hud libvideo-metadata \
	libvideo-defs libarsdk libarsdkctrl libmedia-buffers \
	libmedia-buffers-memory libmedia-buffers-memory-generic libmp4 protobuf \
	libmux

LOCAL_PREREQUISITES := protobuf

LOCAL_BUNDLE_FILES := $(OLYMPE_DEPS_BIN_DIR)protoc
LOCAL_BUNDLE_SYSTEM_DEPS := 1
LOCAL_BUNDLE_SYSTEM_ALLOW_LIST := $(TARGET_PYTHON_WHEEL_ALLOW_LIST)

include $(BUILD_BUNDLE)
