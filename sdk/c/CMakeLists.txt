cmake_minimum_required(VERSION 3.16)
project(cyt-indexer-c VERSION 2.5.3 LANGUAGES C)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CYT_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../..")
set(CYT_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/include")
set(CYT_BUILD_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/scripts/build-c-lib.sh")

option(CYT_BUILD_SHARED "Expose shared library imported target" ON)
option(CYT_BUILD_STATIC "Expose static library imported target" OFF)
option(CYT_BUILD_EXAMPLES "Build examples and register CTest" ON)
option(CYT_INSTALL "Generate install rules" ON)
option(CYT_BUILD_ALL_TARGETS "Build all six Rust triplets" OFF)

if(NOT DEFINED CYT_RUST_TARGET)
    execute_process(
        COMMAND rustc -vV
        OUTPUT_VARIABLE _cyt_rustc_vv
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    string(REGEX MATCH "host: ([^\n]+)" _cyt_host_match "${_cyt_rustc_vv}")
    set(CYT_RUST_TARGET "${CMAKE_MATCH_1}")
endif()

set(_cyt_supported_targets
    x86_64-unknown-linux-gnu
    aarch64-unknown-linux-gnu
    x86_64-apple-darwin
    aarch64-apple-darwin
    x86_64-pc-windows-msvc
    aarch64-pc-windows-msvc
)
list(FIND _cyt_supported_targets "${CYT_RUST_TARGET}" _cyt_target_idx)
if(_cyt_target_idx EQUAL -1)
    message(FATAL_ERROR "CYT_RUST_TARGET must be one of the supported triplets, got: ${CYT_RUST_TARGET}")
endif()

if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(CYT_LIB_PROFILE debug)
else()
    set(CYT_LIB_PROFILE release)
endif()

if(DEFINED ENV{CARGO_TARGET_DIR} AND NOT "$ENV{CARGO_TARGET_DIR}" STREQUAL "")
    set(CYT_TARGET_DIR "$ENV{CARGO_TARGET_DIR}")
else()
    set(CYT_TARGET_DIR "${CYT_REPO_ROOT}/target")
endif()

get_filename_component(CYT_REPO_ROOT "${CYT_REPO_ROOT}" ABSOLUTE)
get_filename_component(CYT_TARGET_DIR "${CYT_TARGET_DIR}" ABSOLUTE)

set(CYT_ARTIFACT_DIR "${CYT_TARGET_DIR}/${CYT_RUST_TARGET}/${CYT_LIB_PROFILE}")
get_filename_component(CYT_ARTIFACT_DIR "${CYT_ARTIFACT_DIR}" ABSOLUTE)

if(CYT_LIB_PROFILE STREQUAL "debug")
    set(_cyt_build_profile_flag --debug)
else()
    set(_cyt_build_profile_flag --release)
endif()

if(WIN32)
    set(CYT_SHARED_LIB "${CYT_ARTIFACT_DIR}/cyt_indexer.dll")
    set(CYT_IMPORT_LIB "${CYT_ARTIFACT_DIR}/cyt_indexer.dll.lib")
    set(CYT_STATIC_LIB "${CYT_ARTIFACT_DIR}/cyt_indexer.lib")
else()
    if(APPLE)
        set(CYT_SHARED_LIB "${CYT_ARTIFACT_DIR}/libcyt_indexer.dylib")
    else()
        set(CYT_SHARED_LIB "${CYT_ARTIFACT_DIR}/libcyt_indexer.so")
    endif()
    set(CYT_STATIC_LIB "${CYT_ARTIFACT_DIR}/libcyt_indexer.a")
endif()

add_custom_command(
    OUTPUT "${CYT_SHARED_LIB}"
    COMMAND bash "${CYT_BUILD_SCRIPT}" --target "${CYT_RUST_TARGET}" ${_cyt_build_profile_flag}
    WORKING_DIRECTORY "${CYT_REPO_ROOT}"
    COMMENT "Building cyt-indexer ffi for ${CYT_RUST_TARGET}"
    VERBATIM
)

add_custom_target(cyt_indexer_build DEPENDS "${CYT_SHARED_LIB}")

if(APPLE)
    set(CYT_DEPS_DYLIB "${CYT_ARTIFACT_DIR}/deps/libcyt_indexer.dylib")
    set(CYT_STAGED_DYLIB "${CMAKE_BINARY_DIR}/libcyt_indexer.dylib")
    add_custom_command(
        OUTPUT "${CYT_STAGED_DYLIB}"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CYT_SHARED_LIB}" "${CYT_STAGED_DYLIB}"
        COMMAND install_name_tool -id "@loader_path/libcyt_indexer.dylib"
            "${CYT_STAGED_DYLIB}"
        DEPENDS cyt_indexer_build
        COMMENT "Stage libcyt_indexer.dylib beside C examples"
    )
    add_custom_target(cyt_stage_dylib DEPENDS "${CYT_STAGED_DYLIB}")
elseif(WIN32)
    set(CYT_STAGED_DLL "${CMAKE_BINARY_DIR}/cyt_indexer.dll")
    add_custom_command(
        OUTPUT "${CYT_STAGED_DLL}"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CYT_SHARED_LIB}" "${CYT_STAGED_DLL}"
        DEPENDS cyt_indexer_build
        COMMENT "Stage cyt_indexer.dll beside C examples"
    )
    add_custom_target(cyt_stage_dll DEPENDS "${CYT_STAGED_DLL}")
endif()

if(CYT_BUILD_SHARED)
    add_library(cyt_indexer SHARED IMPORTED GLOBAL)
    set_target_properties(cyt_indexer PROPERTIES
        IMPORTED_LOCATION "${CYT_SHARED_LIB}"
        INTERFACE_INCLUDE_DIRECTORIES "${CYT_INCLUDE_DIR}"
    )
    if(WIN32)
        set_target_properties(cyt_indexer PROPERTIES
            IMPORTED_IMPLIB "${CYT_IMPORT_LIB}"
        )
    endif()
    add_dependencies(cyt_indexer cyt_indexer_build)
    add_library(CYT::cyt_indexer ALIAS cyt_indexer)
endif()

if(CYT_BUILD_STATIC)
    add_library(cyt_indexer_static STATIC IMPORTED GLOBAL)
    set_target_properties(cyt_indexer_static PROPERTIES
        IMPORTED_LOCATION "${CYT_STATIC_LIB}"
        INTERFACE_INCLUDE_DIRECTORIES "${CYT_INCLUDE_DIR}"
    )
    add_dependencies(cyt_indexer_static cyt_indexer_build)
    add_library(CYT::cyt_indexer_static ALIAS cyt_indexer_static)
endif()

function(cyt_add_example name source)
    add_executable("${name}" "${source}")
    target_include_directories("${name}" PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}"
        "${CYT_INCLUDE_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}/examples")
    target_link_libraries("${name}" PRIVATE CYT::cyt_indexer)
    add_dependencies("${name}" cyt_indexer_build)
    if(WIN32)
        add_dependencies("${name}" cyt_stage_dll)
    elseif(APPLE)
        add_dependencies("${name}" cyt_stage_dylib)
        add_custom_command(TARGET "${name}" POST_BUILD
            COMMAND install_name_tool -change "${CYT_DEPS_DYLIB}"
                "@loader_path/libcyt_indexer.dylib" "$<TARGET_FILE:${name}>"
            COMMAND install_name_tool -change "${CYT_SHARED_LIB}"
                "@loader_path/libcyt_indexer.dylib" "$<TARGET_FILE:${name}>"
            COMMENT "Fix ${name} dylib load path"
        )
    endif()
endfunction()

if(CYT_BUILD_EXAMPLES)
    enable_testing()
    cyt_add_example(cyt_example_basic examples/basic.c)
    cyt_add_example(cyt_example_error_handling examples/error_handling.c)
    cyt_add_example(cyt_example_retrieve examples/retrieve.c)
    cyt_add_example(cyt_example_policies examples/policies.c)
    cyt_add_example(cyt_example_pipeline examples/pipeline.c)
    cyt_add_example(cyt_example_skills examples/skills.c)

    add_test(NAME cyt_basic COMMAND cyt_example_basic)
    add_test(NAME cyt_error_handling COMMAND cyt_example_error_handling)
    add_test(NAME cyt_retrieve COMMAND cyt_example_retrieve)
    add_test(NAME cyt_policies COMMAND cyt_example_policies)
    add_test(NAME cyt_pipeline COMMAND cyt_example_pipeline)
    add_test(NAME cyt_skills COMMAND cyt_example_skills)
endif()

include(GNUInstallDirs)

if(CYT_INSTALL)
    install(FILES "${CYT_INCLUDE_DIR}/cyt_indexer.h"
        DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/cyt"
    )
    if(CYT_BUILD_SHARED)
        install(FILES "${CYT_SHARED_LIB}"
            DESTINATION "${CMAKE_INSTALL_LIBDIR}/${CYT_RUST_TARGET}"
        )
        if(WIN32)
            install(FILES "${CYT_IMPORT_LIB}"
                DESTINATION "${CMAKE_INSTALL_LIBDIR}/${CYT_RUST_TARGET}"
            )
        endif()
    endif()
    if(CYT_BUILD_STATIC)
        install(FILES "${CYT_STATIC_LIB}"
            DESTINATION "${CMAKE_INSTALL_LIBDIR}/${CYT_RUST_TARGET}"
        )
    endif()

    include(CMakePackageConfigHelpers)
    configure_package_config_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/cmake/CYTConfig.cmake.in"
        "${CMAKE_CURRENT_BINARY_DIR}/CYTConfig.cmake"
        INSTALL_DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/CYT"
        PATH_VARS CYT_TARGET_DIR CYT_INCLUDE_DIR
    )
    write_basic_package_version_file(
        "${CMAKE_CURRENT_BINARY_DIR}/CYTConfigVersion.cmake"
        VERSION "${PROJECT_VERSION}"
        COMPATIBILITY SameMajorVersion
    )
    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/CYTConfig.cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/CYTConfigVersion.cmake"
        DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/CYT"
    )
endif()
