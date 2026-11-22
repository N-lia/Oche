plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    // Screenshot tests of the Dynamic Island on the JVM (no emulator).
    id("app.cash.paparazzi")
}

// Optional default gateway baked into the debug build, e.g.
//   ./gradlew assembleDebug -POYI_DEFAULT_GATEWAY_URL=https://oyi.example
val defaultGatewayUrl: String =
    (project.findProperty("OYI_DEFAULT_GATEWAY_URL") as String?) ?: ""

// The CI run number (see .github/workflows/android.yml), or 1 locally.
val buildNumber: Int = System.getenv("OYI_BUILD_NUMBER")?.toIntOrNull() ?: 1

android {
    namespace = "ai.oyi.device"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.oyi.device"
        minSdk = 26
        targetSdk = 35
        // CI stamps each build with its run number, so the in-app updater can
        // tell which build is newer. Local builds are 1.
        versionCode = buildNumber
        versionName = "0.1.$buildNumber"
        vectorDrawables { useSupportLibrary = true }
        buildConfigField("String", "DEFAULT_GATEWAY_URL", "\"$defaultGatewayUrl\"")
    }

    // One fixed debug key for every build. Android only installs an update
    // signed with the same key as the installed app, and CI would otherwise
    // make a fresh random debug key on every run. Debug-only: never use it
    // for a Play Store release.
    signingConfigs {
        getByName("debug") {
            storeFile = file("oyi-debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("debug")
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    // Material 3 Expressive shapes: polygons and morphs for the loader and buttons.
    implementation("androidx.graphics:graphics-shapes:1.0.1")
    // Backdrop blur for the liquid-glass surfaces (pinned to match Compose 1.7).
    implementation("dev.chrisbanes.haze:haze:1.1.1")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("junit:junit:4.13.2")
    // The real org.json, so JSON parsing can be unit tested off-device.
    testImplementation("org.json:json:20240303")
}
