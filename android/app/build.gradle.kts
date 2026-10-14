import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}
val signingFile = rootProject.file("../private/android/signing.properties")
val signing = Properties().apply { if (signingFile.exists()) signingFile.inputStream().use { load(it) } }
val clientContractResources by tasks.registering(Sync::class) {
    from(rootProject.file("../web/backend/tests/fixtures/client-contract.properties"))
    into(layout.buildDirectory.dir("generated/client-contract"))
}
android {
    namespace = "ru.veilway.android"
    compileSdk = 37
    buildToolsVersion = "36.0.0"
    ndkVersion = "27.2.12479018"
    defaultConfig {
        applicationId = "ru.veilway.android"
        minSdk = 29
        targetSdk = 35
        versionCode = 4
        versionName = "0.1.3-pilot"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        externalNativeBuild { cmake { cppFlags += "-std=c++17" } }
    }
    signingConfigs {
        if (signingFile.exists()) create("pilot") {
            storeFile = file(signing.getProperty("storeFile"))
            storePassword = signing.getProperty("storePassword")
            keyAlias = "veilway-pilot"
            keyPassword = signing.getProperty("storePassword")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (signingFile.exists()) signingConfig = signingConfigs.getByName("pilot")
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    buildFeatures { compose = true; buildConfig = true }
    externalNativeBuild { cmake { path = file("src/main/cpp/CMakeLists.txt"); version = "3.22.1" } }
    packaging { resources.excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1") }
    sourceSets.getByName("test").resources.directories.add(
        layout.buildDirectory.dir("generated/client-contract").get().asFile.absolutePath
    )
}
tasks.matching { it.name.startsWith("process") && it.name.endsWith("UnitTestJavaRes") }.configureEach {
    dependsOn(clientContractResources)
}
dependencies {
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.browser:browser:1.10.0")
    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.material3:material3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
}
dependencyLocking { lockAllConfigurations() }
