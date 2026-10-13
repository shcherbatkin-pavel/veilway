cmake_minimum_required(VERSION 3.22.1)
project(veilway_openvpn LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(DEPS "${CMAKE_CURRENT_SOURCE_DIR}/../../../../third-party/sources")
set(CORE "${DEPS}/openvpn3")
if(NOT EXISTS "${CORE}/client/ovpncli.cpp")
    message(FATAL_ERROR "Run scripts/prepare-android-native.py --download --sdk-root SDK first")
endif()
add_library(veilway_openvpn SHARED bridge.cpp "${CORE}/client/ovpncli.cpp"
    "${CORE}/openvpn/crypto/data_epoch.cpp" "${DEPS}/lz4/lib/lz4.c")
target_compile_definitions(veilway_openvpn PRIVATE ASIO_STANDALONE USE_ASIO USE_OPENSSL HAVE_LZ4
    OPENVPN_CORE_API_VISIBILITY_HIDDEN)
target_compile_options(veilway_openvpn PRIVATE -fvisibility=hidden -fstack-protector-strong)
target_include_directories(veilway_openvpn PRIVATE "${CORE}" "${DEPS}/asio/asio/include"
    "${DEPS}/lz4/lib" "${DEPS}/openssl-${ANDROID_ABI}/include")
target_link_libraries(veilway_openvpn "${DEPS}/openssl-${ANDROID_ABI}/lib/libssl.a"
    "${DEPS}/openssl-${ANDROID_ABI}/lib/libcrypto.a" log)
target_link_options(veilway_openvpn PRIVATE "-Wl,-z,max-page-size=16384")
