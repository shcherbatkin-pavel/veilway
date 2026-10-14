# Veilway Android dependencies

Veilway's Kotlin/JNI adapter remains under the repository MIT license. OpenVPN
3 Core is used under **MPL-2.0**, one of its upstream licensing alternatives.
Its original copyright/SPDX notices remain intact. No upstream source is
patched. The full, checksum-pinned source archives accompany the pilot APK in
`veilway-<version>-sources.tar.gz`; offer the matching source bundle with every APK.

Native versions, origins and SHA-256 digests are in `dependencies.json`:

- OpenVPN 3 Core: Copyright OpenVPN Inc.; MPL-2.0 alternative selected.
- Asio: Copyright Christopher M. Kohlhoff; Boost Software License 1.0.
- LZ4: Copyright Yann Collet; BSD 2-Clause for the embedded library.
- OpenSSL: Copyright the OpenSSL Project Authors; Apache License 2.0.

License texts are also embedded in APK assets under `licenses/`. Kotlin,
kotlinx.coroutines and AndroidX runtime libraries use Apache License 2.0.
Gradle's transitive artifact versions and checksums are retained in
`app/gradle.lockfile` and `gradle/verification-metadata.xml`.

Sources, build output, APKs and signing keys stay outside public Git. The
ignored native source directory is regenerated only with an explicit
`prepare-android-native.py --download` invocation. This notice and source
manifest are public and contain no operator/VPN materials.
