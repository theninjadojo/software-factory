# Android build and test image for verification workers (docs/workers.md). Built by the operator once; used by worker/recipes/android-test.sh.
#
#   docker build -t factory-android sandbox/android          (add --build-arg WITH_EMULATOR=1 for emulator tests: about 1.5 GB more)
#
# Release installs pull a prebuilt ghcr.io/<owner>/shikumi-android instead (scripts/setup-worker.sh does it). The command-line tools zip
# is pinned by version AND checksum. The checksum below is the one Google publishes at https://developer.android.com/studio for
# commandlinetools-linux-15859902_latest.zip (checked against a fresh download). To move to a newer version, change BOTH arguments:
# the build fails rather than trusting an unverified download.
FROM eclipse-temurin:17.0.13_11-jdk-jammy

ARG CMDLINE_TOOLS_VERSION=15859902
ARG CMDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
ARG PLATFORM=android-34
ARG BUILD_TOOLS=34.0.0
ARG WITH_EMULATOR=0
ARG SYSTEM_IMAGE=system-images;android-34;google_apis;x86_64

ENV ANDROID_HOME=/opt/android-sdk
ENV SYSTEM_IMAGE="${SYSTEM_IMAGE}"
ENV PATH=${PATH}:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/emulator

RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates libpulse0 libnss3 libgl1 \
    && rm -rf /var/lib/apt/lists/*
RUN test -n "$CMDLINE_TOOLS_SHA256" || { echo "set --build-arg CMDLINE_TOOLS_SHA256=<sha256 of the command-line tools zip>" >&2; exit 1; } \
    && mkdir -p /opt/android-sdk/cmdline-tools \
    && curl -fsSL -o /tmp/tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_VERSION}_latest.zip" \
    && echo "${CMDLINE_TOOLS_SHA256}  /tmp/tools.zip" | sha256sum -c - \
    && unzip -q /tmp/tools.zip -d /opt/android-sdk/cmdline-tools && mv /opt/android-sdk/cmdline-tools/cmdline-tools /opt/android-sdk/cmdline-tools/latest \
    && rm /tmp/tools.zip
RUN yes | sdkmanager --licenses >/dev/null \
    && sdkmanager "platform-tools" "platforms;${PLATFORM}" "build-tools;${BUILD_TOOLS}" \
    && if [ "$WITH_EMULATOR" = "1" ]; then sdkmanager "emulator" "${SYSTEM_IMAGE}"; fi \
    && chmod -R a+rX /opt/android-sdk

COPY android-run.sh /usr/local/bin/android-run
RUN chmod 755 /usr/local/bin/android-run
# No USER line: the recipe runs the container with --user <the worker's uid> so files in the checkout stay owned by the worker.
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/android-run"]
