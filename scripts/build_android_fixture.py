"""Build the small native Android fixture using an existing SDK and JDK."""

import os
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sdk = Path(os.environ["ANDROID_HOME"])
    java = Path(os.environ["JAVA_HOME"]) / "bin"
    tools = sdk / "build-tools" / "35.0.0"
    platform = sdk / "platforms" / "android-35" / "android.jar"
    output = ROOT / "data" / "android-build"
    output.mkdir(parents=True, exist_ok=True)
    for name in ["classes", "dex"]:
        (output / name).mkdir(exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    script = ".bat" if os.name == "nt" else ""

    def run(*argv):
        subprocess.run([str(v) for v in argv], check=True, cwd=ROOT)

    run(
        java / f"javac{suffix}",
        "-source",
        "8",
        "-target",
        "8",
        "-classpath",
        platform,
        "-d",
        output / "classes",
        ROOT / "examples/android/MainActivity.java",
    )
    run(
        tools / f"d8{script}",
        "--lib",
        platform,
        "--output",
        output / "dex",
        *sorted((output / "classes").rglob("*.class")),
    )
    run(
        tools / f"aapt2{suffix}",
        "link",
        "-o",
        output / "unsigned.apk",
        "-I",
        platform,
        "--manifest",
        ROOT / "examples/android/AndroidManifest.xml",
        "--min-sdk-version",
        "26",
        "--target-sdk-version",
        "35",
    )
    with zipfile.ZipFile(output / "unsigned.apk", "a") as archive:
        archive.write(output / "dex/classes.dex", "classes.dex")
    run(tools / f"zipalign{suffix}", "-f", "4", output / "unsigned.apk", output / "aligned.apk")
    key = output / "debug.keystore"
    if not key.exists():
        run(
            java / f"keytool{suffix}",
            "-genkeypair",
            "-keystore",
            key,
            "-storepass",
            "android",
            "-keypass",
            "android",
            "-alias",
            "debug",
            "-dname",
            "CN=OmniTest Local Fixture",
            "-keyalg",
            "RSA",
            "-validity",
            "3650",
        )
    apk = output / "fixture.apk"
    run(
        tools / f"apksigner{script}",
        "sign",
        "--ks",
        key,
        "--ks-pass",
        "pass:android",
        "--out",
        apk,
        output / "aligned.apk",
    )
    run(tools / f"apksigner{script}", "verify", apk)
    print(apk)


if __name__ == "__main__":
    main()
