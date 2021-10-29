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

include $(BUILD_CUSTOM)

include $(CLEAR_VARS)

LOCAL_MODULE := olympe
LOCAL_CATEGORY_PATH := libs
LOCAL_DESCRIPTION := Drone controller python library based on ctypes bindings of libpdraw and arsdk-ng
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
