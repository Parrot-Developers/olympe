LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe-base
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := Olympe pure python module
LOCAL_DEPENDS_MODULES := python arsdkparser

PRIVATE_OLYMPE_OUT_DIR=$(TARGET_OUT_STAGING)$(shell echo $${TARGET_DEPLOY_ROOT:-/usr})

olympe_source_files := \
    $(call all-files-under,src,.py) \
    $(call all-files-under,src,.rst) \
    $(call all-files-under,src,.yaml)

# Install files in python site-packages staging directory
LOCAL_COPY_FILES := \
	$(foreach __f,$(olympe_source_files), \
		$(__f):$(PRIVATE_OLYMPE_OUT_DIR)/lib/python/site-packages/$(strip $(patsubst src/%, %, $(__f))) \
	)

include $(BUILD_CUSTOM)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := Drone controller python library based on ctypes bindings of libpdraw and arsdk-ng
LOCAL_DEPENDS_MODULES := python arsdkparser olympe-base olympe-deps

include $(BUILD_CUSTOM)
