// Veilway JNI adapter. Upstream OpenVPN core is used under MPL-2.0.
#include <jni.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <openvpn/io/io.hpp>
#include <client/ovpncli.hpp>

using namespace openvpn::ClientAPI;

class Client final : public OpenVPNClient {
    JavaVM* vm;
    jobject service;
    jclass cls;
    JNIEnv* env = nullptr; // run() callbacks all execute on its attached JVM thread
public:
    std::atomic<bool> cancelled{false};
    Client(JNIEnv* e, jobject owner) {
        e->GetJavaVM(&vm);
        service = e->NewGlobalRef(owner);
        cls = static_cast<jclass>(e->NewGlobalRef(e->GetObjectClass(owner)));
    }
    ~Client() override {
        JNIEnv* e = nullptr;
        bool attach = vm->GetEnv(reinterpret_cast<void**>(&e), JNI_VERSION_1_6) != JNI_OK;
        if (attach) vm->AttachCurrentThread(&e, nullptr);
        e->DeleteGlobalRef(service); e->DeleteGlobalRef(cls);
        if (attach) vm->DetachCurrentThread();
    }
    bool okay() { if (env->ExceptionCheck()) { env->ExceptionClear(); return false; } return true; }
    bool call0(const char* method) {
        auto id = env->GetMethodID(cls, method, "()Z");
        if (!id || !okay()) return false;
        bool result = env->CallBooleanMethod(service, id);
        return okay() && result;
    }
    bool callInt(const char* method, int value) {
        auto id = env->GetMethodID(cls, method, "(I)Z");
        if (!id || !okay()) return false;
        bool result = env->CallBooleanMethod(service, id, value);
        return okay() && result;
    }
    bool callString(const char* method, const std::string& value) {
        auto id = env->GetMethodID(cls, method, "(Ljava/lang/String;)Z");
        if (!id || !okay()) return false;
        auto str = env->NewStringUTF(value.c_str());
        bool result = env->CallBooleanMethod(service, id, str);
        env->DeleteLocalRef(str);
        return okay() && result;
    }
    bool address(const char* method, const std::string& value, int prefix) {
        auto id = env->GetMethodID(cls, method, "(Ljava/lang/String;I)Z");
        if (!id || !okay()) return false;
        auto str = env->NewStringUTF(value.c_str());
        bool result = env->CallBooleanMethod(service, id, str, prefix);
        env->DeleteLocalRef(str);
        return okay() && result;
    }
    void notify(const std::string& name) { callString("nativeEvent", name); }
    void run(JNIEnv* e, const std::string& profile) {
        env = e;
        try {
            Config config;
            config.content = profile;
            config.tunPersist = true;
            config.allowUnusedAddrFamilies = "no";
            config.compressionMode = "no";
            config.clockTickMS = 1000;
            auto parsed = eval_config(config);
            if (parsed.error || !parsed.autologin || parsed.externalPki || parsed.privateKeyPasswordRequired) {
                notify("PROFILE_REJECTED"); return;
            }
            if (cancelled) return;
            auto status = connect();
            if (status.error) notify("TUNNEL_FAILED");
        } catch (...) { notify("TUNNEL_FAILED"); }
    }
    bool socket_protect(openvpn_io::detail::socket_type fd, std::string, bool) override { return callInt("protectSocket", fd); }
    bool tun_builder_new() override { return call0("newBuilder"); }
    bool tun_builder_set_remote_address(const std::string&, bool) override { return true; }
    bool tun_builder_add_address(const std::string& a, int p, const std::string&, bool, bool) override { return address("addAddress", a, p); }
    bool tun_builder_add_route(const std::string& a, int p, int, bool) override { return address("addRoute", a, p); }
    bool tun_builder_reroute_gw(bool v4, bool v6, unsigned int) override {
        return (!v4 || address("addRoute", "0.0.0.0", 0)) && (!v6 || address("addRoute", "::", 0));
    }
    // No route bypass: selected apps must never leave the VPN via local/excluded networks.
    bool tun_builder_exclude_route(const std::string&, int, int, bool) override { return true; }
    bool tun_builder_set_dns_options(const openvpn::DnsOptions& dns) override {
        bool found = false;
        for (const auto& [priority, server] : dns.servers) {
            for (const auto& a : server.addresses) {
                if (a.port != 0 && a.port != 53) return false;
                if (!callString("addDns", a.address)) return false;
                found = true;
            }
        }
        return found;
    }
    bool tun_builder_set_mtu(int mtu) override { return callInt("setMtu", mtu); }
    bool tun_builder_set_session_name(const std::string&) override { return true; }
    int tun_builder_establish() override {
        auto id = env->GetMethodID(cls, "establish", "()I");
        if (!id || !okay()) return -1;
        int fd = env->CallIntMethod(service, id);
        return okay() ? fd : -1;
    }
    bool tun_builder_persist() override { return true; }
    void tun_builder_teardown(bool) override {} // service retains its FD until explicit disconnect
    bool pause_on_connection_timeout() override { return false; }
    void event(const Event& e) override { notify(e.name); }
    void acc_event(const AppCustomControlMessageEvent&) override {}
    void log(const LogInfo&) override {} // no profiles, addresses, diagnostics or packet data in logs
    void external_pki_cert_request(ExternalPKICertRequest& r) override { r.error = true; }
    void external_pki_sign_request(ExternalPKISignRequest& r) override { r.error = true; }
    void clock_tick() override { if (cancelled) stop(); }
};

static std::mutex registryMutex;
static std::unordered_map<jlong, std::shared_ptr<Client>> registry;
static jlong nextHandle = 1;
static std::shared_ptr<Client> get(jlong handle) {
    std::lock_guard<std::mutex> lock(registryMutex);
    auto it = registry.find(handle);
    return it == registry.end() ? nullptr : it->second;
}
extern "C" JNIEXPORT jlong JNICALL Java_ru_veilway_android_NativeCore_create(JNIEnv* env, jobject, jobject service) {
    std::lock_guard<std::mutex> lock(registryMutex);
    jlong handle = nextHandle++;
    registry[handle] = std::make_shared<Client>(env, service);
    return handle;
}
extern "C" JNIEXPORT void JNICALL Java_ru_veilway_android_NativeCore_run(JNIEnv* env, jobject, jlong handle, jstring profile) {
    auto client = get(handle);
    if (!client) return;
    const char* data = env->GetStringUTFChars(profile, nullptr);
    std::string content(data);
    env->ReleaseStringUTFChars(profile, data);
    client->run(env, content);
    std::fill(content.begin(), content.end(), '\0');
}
extern "C" JNIEXPORT void JNICALL Java_ru_veilway_android_NativeCore_stop(JNIEnv*, jobject, jlong handle) {
    auto client = get(handle);
    if (client) { client->cancelled = true; client->stop(); }
}
extern "C" JNIEXPORT void JNICALL Java_ru_veilway_android_NativeCore_reconnect(JNIEnv*, jobject, jlong handle) {
    auto client = get(handle);
    if (client) client->reconnect(0);
}
extern "C" JNIEXPORT void JNICALL Java_ru_veilway_android_NativeCore_destroy(JNIEnv*, jobject, jlong handle) {
    std::lock_guard<std::mutex> lock(registryMutex);
    registry.erase(handle);
}
