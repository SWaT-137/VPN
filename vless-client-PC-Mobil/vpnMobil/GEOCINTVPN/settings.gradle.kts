pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        flatDir {
            dirs("v2ray/libs")
        }
        google()
        mavenCentral()
    }
}

rootProject.name = "GEOCINTVPN"
include(":app")
include(":v2ray") // <--- ВОТ ЭТОЙ СТРОКИ У ТЕБЯ НЕ ХВАТАЛО! Добавь её обязательно!