plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val channel = providers.gradleProperty("buildChannel").orElse("dev").get()
require(channel in listOf("stable", "dev")) { "Invalid build channel" }
val releaseVersion = rootProject.file("release/version.txt").readText().trim()
require(releaseVersion.matches(Regex("[0-9]+\\.[0-9]+\\.[0-9]+"))) { "Invalid release version" }
val buildCode = providers.gradleProperty("buildVersionCode").orElse("1").get().toInt()
require(buildCode in 1..2100000000) { "Invalid versionCode" }
fun gitValue(vararg args: String): String = providers.exec {
    workingDir(rootProject.projectDir)
    commandLine("git", *args)
}.standardOutput.asText.get().trim()
val revision = gitValue("rev-parse", "HEAD")
val modified = gitValue("status", "--porcelain").isNotEmpty()
val publishBuild = providers.gradleProperty("publishBuild").orElse("false").get() == "true"
if (publishBuild) {
    require(!modified) { "Published builds require a clean checkout" }
    require(providers.gradleProperty("buildSourceRevision").get() == revision) { "Source revision mismatch" }
    require(providers.gradleProperty("buildVersionCode").isPresent) { "Allocated versionCode required" }
}
val sourceIdentity = revision + if (modified) " (modified)" else ""
val appVersion = if (channel == "stable") releaseVersion else
    "$releaseVersion-dev.$buildCode.g${revision.take(12)}"
val identityAssets = layout.buildDirectory.dir("generated/identityAssets")
val generateIdentity by tasks.registering {
    val receipt = identityAssets.map { it.file("build-identity.json") }
    outputs.file(receipt)
    inputs.property("revision", sourceIdentity)
    inputs.property("channel", channel)
    inputs.property("versionCode", buildCode)
    inputs.property("versionName", appVersion)
    doLast {
        receipt.get().asFile.apply {
            parentFile.mkdirs()
            writeText("""{"source":"$sourceIdentity","channel":"$channel","versionCode":$buildCode,"versionName":"$appVersion"}""")
        }
    }
}

android {
    namespace = "software.zeasy.momir"
    compileSdk = 34

    defaultConfig {
        applicationId = "io.github.moobaloo.momir" + if (channel == "dev") ".dev" else ""
        // The Sunmi V2 ships Android 7.1.1. That is the whole reason this app is
        // Views-and-Canvas rather than Compose: 909 MB of RAM on an armeabi-v7a
        // chip does not enjoy a recomposition loop.
        minSdk = 25
        targetSdk = 34
        versionCode = buildCode
        versionName = appVersion
        resValue("string", "app_name", if (channel == "dev") "Momir Dev" else "Momir")
        buildConfigField("String", "SOURCE_REVISION", "\"$sourceIdentity\"")
        buildConfigField("String", "BUILD_CHANNEL", "\"$channel\"")
    }

    buildFeatures {
        aidl = true
        viewBinding = true
        buildConfig = true
    }

    sourceSets.getByName("main").assets.srcDir(identityAssets)

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-local"
            resValue("string", "app_name", "Momir Dev")
        }
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.0.4")

    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.google.android.material:material:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Pure-Java QR encoder. No camera, no Android bindings, ~100 KB.
    implementation("com.google.zxing:core:3.5.3")
}

tasks.named("preBuild").configure { dependsOn(generateIdentity) }
