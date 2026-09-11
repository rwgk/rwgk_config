#!/bin/bash
#
# Download standalone CUDA Toolkit installers without navigating the web UI.
#
# Supported platform names:
#   linux-amd64, linux-arm64, windows-amd64, windows-arm64
#
# Examples:
#   cd some-directory
#   ctk_download_installers.sh 13.4.1
#   ctk_download_installers.sh 12.8.2 linux-arm64
#   ctk_download_installers.sh list 13.4.1
#   ctk_download_installers.sh url 13.4.1 windows-arm64
#   ctk_download_installers.sh sync --purge
#
# "sync" selects the numerically latest patch release in every CUDA 12/13
# major.minor line. With --purge, and only after every wanted file verifies,
# it removes obsolete top-level cuda_12*.run/.exe and cuda_13*.run/.exe files.
# It never removes subdirectories, unrelated files, or .part downloads.
#
# Agent maintenance instructions:
# 1. Treat NVIDIA's CUDA Toolkit Archive as authoritative:
#      https://developer.nvidia.com/cuda-toolkit-archive
#    Discover machine-readable versions with:
#      curl -fsSL https://developer.download.nvidia.com/compute/cuda/redist/ |
#          ctk_versions_list.py
#    Note: ctk_versions_list.py intentionally reads piped stdin. In a
#    non-interactive agent shell, invoking it without the pipe may return no
#    output because stdin is not a TTY.
# 2. For each new CUDA 12.x or 13.x version, fetch:
#      https://developer.nvidia.com/cuda-X-Y-Z-download-archive
#    The current release may also be at https://developer.nvidia.com/cuda-downloads.
#    Parse the NestedOptionSelector data-react-props payload; do not guess names.
# 3. Add one catalog row for each published local standalone installer among:
#      Linux x86_64 runfile       -> linux-amd64
#      Linux arm64-sbsa runfile  -> linux-arm64
#      Windows x86_64 local EXE  -> windows-amd64
#      Windows ARM64 local EXE   -> windows-arm64
#    Exclude network installers, packages, cross installers, and Tegra/Jetson.
#    Preserve developer-preview URLs if publicly archived; these sometimes use
#    packages.nvidia.com/prerelease rather than developer.download.nvidia.com.
# 4. Copy version, filename, md5sum, byte size, and exact URL from NVIDIA's
#    release payload. Keep rows sorted by version (-V) and platform. The sync
#    policy derives latest patches automatically; do not mark them manually.
# 5. Validate changes with:
#      ctk_download_installers.sh list
#      ctk_download_installers.sh list VERSION
#      ctk_download_installers.sh url VERSION PLATFORM
#      command shellcheck bin/ctk_download_installers.sh
#    Then inspect the current myshfmt alias in ~/rwgk_config/bashrc and apply
#    that exact formatter, as required by ~/rwgk_config/AGENTS.md. Also inspect
#    ~/rwgk_config_nvidia for callers before changing CLI behavior.
#
set -euo pipefail

readonly PROGRAM=${0##*/}

usage() {
    cat <<EOF
Usage:
  $PROGRAM list [VERSION [PLATFORM]]
  $PROGRAM url VERSION PLATFORM
  $PROGRAM VERSION [PLATFORM]
  $PROGRAM sync [--purge]

PLATFORM is linux-amd64, linux-arm64, windows-amd64, windows-arm64,
or all (the default). Downloads always go to the current directory.
EOF
}

die() {
    echo "$PROGRAM: $*" >&2
    exit 1
}

catalog() {
    cat <<'CATALOG'
12.0.0	linux-amd64	cuda_12.0.0_525.60.13_linux.run	8ce2e08f343dd47032b43d961e3fecfa	4123387911	https://developer.download.nvidia.com/compute/cuda/12.0.0/local_installers/cuda_12.0.0_525.60.13_linux.run
12.0.0	linux-arm64	cuda_12.0.0_525.60.13_linux_sbsa.run	988debe0a0404f045898e3940b7e9423	3228170784	https://developer.download.nvidia.com/compute/cuda/12.0.0/local_installers/cuda_12.0.0_525.60.13_linux_sbsa.run
12.0.0	windows-amd64	cuda_12.0.0_527.41_windows.exe	88d9b91657ee832692c7465bee3d6588	3660532472	https://developer.download.nvidia.com/compute/cuda/12.0.0/local_installers/cuda_12.0.0_527.41_windows.exe
12.0.1	linux-amd64	cuda_12.0.1_525.85.12_linux.run	2a5b80f322151e25ed792029e4571318	4207617207	https://developer.download.nvidia.com/compute/cuda/12.0.1/local_installers/cuda_12.0.1_525.85.12_linux.run
12.0.1	linux-arm64	cuda_12.0.1_525.85.12_linux_sbsa.run	b7ac798f56c016278669878967c760aa	3311104312	https://developer.download.nvidia.com/compute/cuda/12.0.1/local_installers/cuda_12.0.1_525.85.12_linux_sbsa.run
12.0.1	windows-amd64	cuda_12.0.1_528.33_windows.exe	b6cfa95af8ac5f03eee98307b2f66507	3678256040	https://developer.download.nvidia.com/compute/cuda/12.0.1/local_installers/cuda_12.0.1_528.33_windows.exe
12.1.0	linux-amd64	cuda_12.1.0_530.30.02_linux.run	6b6583fad98b905574a1fd37a471ef92	4245586997	https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_530.30.02_linux.run
12.1.0	linux-arm64	cuda_12.1.0_530.30.02_linux_sbsa.run	30181d954616196af22c07a0af277452	3380436775	https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_530.30.02_linux_sbsa.run
12.1.0	windows-amd64	cuda_12.1.0_531.14_windows.exe	f9ce2affc49b82a220c292f734a57af5	3350873192	https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_531.14_windows.exe
12.1.1	linux-amd64	cuda_12.1.1_530.30.02_linux.run	2f0a4127bf797bf4eab0be2a547cb8d0	4317456991	https://developer.download.nvidia.com/compute/cuda/12.1.1/local_installers/cuda_12.1.1_530.30.02_linux.run
12.1.1	linux-arm64	cuda_12.1.1_530.30.02_linux_sbsa.run	cb16e348722ebef28b3f9838dc877d74	3452801028	https://developer.download.nvidia.com/compute/cuda/12.1.1/local_installers/cuda_12.1.1_530.30.02_linux_sbsa.run
12.1.1	windows-amd64	cuda_12.1.1_531.14_windows.exe	7da46c38277ab61b78c24d267c5bacae	3402783600	https://developer.download.nvidia.com/compute/cuda/12.1.1/local_installers/cuda_12.1.1_531.14_windows.exe
12.2.0	linux-amd64	cuda_12.2.0_535.54.03_linux.run	72aefb16c35ecb91074b85c27fa26e46	4315928767	https://developer.download.nvidia.com/compute/cuda/12.2.0/local_installers/cuda_12.2.0_535.54.03_linux.run
12.2.0	linux-arm64	cuda_12.2.0_535.54.03_linux_sbsa.run	725e8fc29b912d6f7add6c93fe84544b	3498931409	https://developer.download.nvidia.com/compute/cuda/12.2.0/local_installers/cuda_12.2.0_535.54.03_linux_sbsa.run
12.2.0	windows-amd64	cuda_12.2.0_536.25_windows.exe	4a0f7daaf0a961030a144e853e32d7d5	3219432104	https://developer.download.nvidia.com/compute/cuda/12.2.0/local_installers/cuda_12.2.0_536.25_windows.exe
12.2.1	linux-amd64	cuda_12.2.1_535.86.10_linux.run	2579fcb61cd80d3284d73da93785550f	4332490379	https://developer.download.nvidia.com/compute/cuda/12.2.1/local_installers/cuda_12.2.1_535.86.10_linux.run
12.2.1	linux-arm64	cuda_12.2.1_535.86.10_linux_sbsa.run	a214b118b29bf560567b2c2d03e51dbd	3509827352	https://developer.download.nvidia.com/compute/cuda/12.2.1/local_installers/cuda_12.2.1_535.86.10_linux_sbsa.run
12.2.1	windows-amd64	cuda_12.2.1_536.67_windows.exe	f4c0b5d16c02ee5f53b9ad8e84a03f20	3221759248	https://developer.download.nvidia.com/compute/cuda/12.2.1/local_installers/cuda_12.2.1_536.67_windows.exe
12.2.2	linux-amd64	cuda_12.2.2_535.104.05_linux.run	a32d82924645e0369c14acaebd54bff1	4344134690	https://developer.download.nvidia.com/compute/cuda/12.2.2/local_installers/cuda_12.2.2_535.104.05_linux.run
12.2.2	linux-arm64	cuda_12.2.2_535.104.05_linux_sbsa.run	bcb992a8743efb525c22687c2336483c	3521001849	https://developer.download.nvidia.com/compute/cuda/12.2.2/local_installers/cuda_12.2.2_535.104.05_linux_sbsa.run
12.2.2	windows-amd64	cuda_12.2.2_537.13_windows.exe	2bd993797152533039048d43b8977ea5	3227197584	https://developer.download.nvidia.com/compute/cuda/12.2.2/local_installers/cuda_12.2.2_537.13_windows.exe
12.3.0	linux-amd64	cuda_12.3.0_545.23.06_linux.run	848b8caed9a91ddf23a325f6bc1fdbf9	4360403711	https://developer.download.nvidia.com/compute/cuda/12.3.0/local_installers/cuda_12.3.0_545.23.06_linux.run
12.3.0	linux-arm64	cuda_12.3.0_545.23.06_linux_sbsa.run	d07560c8686084a199b2fdecba1d0815	3534718263	https://developer.download.nvidia.com/compute/cuda/12.3.0/local_installers/cuda_12.3.0_545.23.06_linux_sbsa.run
12.3.0	windows-amd64	cuda_12.3.0_545.84_windows.exe	18d566e8ab5221c2eca0b5f663b998f0	3279101960	https://developer.download.nvidia.com/compute/cuda/12.3.0/local_installers/cuda_12.3.0_545.84_windows.exe
12.3.1	linux-amd64	cuda_12.3.1_545.23.08_linux.run	cec391f9a1a3d1a62f354ac6cb2f82ec	4368526618	https://developer.download.nvidia.com/compute/cuda/12.3.1/local_installers/cuda_12.3.1_545.23.08_linux.run
12.3.1	linux-arm64	cuda_12.3.1_545.23.08_linux_sbsa.run	9a56936b2c52a3ceedf9be2f927317a2	3543476599	https://developer.download.nvidia.com/compute/cuda/12.3.1/local_installers/cuda_12.3.1_545.23.08_linux_sbsa.run
12.3.1	windows-amd64	cuda_12.3.1_546.12_windows.exe	c17b67869681aaa6467b1495d6f5948d	3281693944	https://developer.download.nvidia.com/compute/cuda/12.3.1/local_installers/cuda_12.3.1_546.12_windows.exe
12.3.2	linux-amd64	cuda_12.3.2_545.23.08_linux.run	9d3585b651f4909f72c4db379beb3e01	4368514070	https://developer.download.nvidia.com/compute/cuda/12.3.2/local_installers/cuda_12.3.2_545.23.08_linux.run
12.3.2	linux-arm64	cuda_12.3.2_545.23.08_linux_sbsa.run	7deb8a60af6407caf04692639d8779d3	3543490985	https://developer.download.nvidia.com/compute/cuda/12.3.2/local_installers/cuda_12.3.2_545.23.08_linux_sbsa.run
12.3.2	windows-amd64	cuda_12.3.2_546.12_windows.exe	19d3bfcbeaa7c88d6b4990df857e9c97	3279199688	https://developer.download.nvidia.com/compute/cuda/12.3.2/local_installers/cuda_12.3.2_546.12_windows.exe
12.4.0	linux-amd64	cuda_12.4.0_550.54.14_linux.run	5148982e9f2d13a387f96a956e61aa2e	4454353277	https://developer.download.nvidia.com/compute/cuda/12.4.0/local_installers/cuda_12.4.0_550.54.14_linux.run
12.4.0	linux-arm64	cuda_12.4.0_550.54.14_linux_sbsa.run	239b1206d4e9d41893d1876319a601e4	3655060056	https://developer.download.nvidia.com/compute/cuda/12.4.0/local_installers/cuda_12.4.0_550.54.14_linux_sbsa.run
12.4.0	windows-amd64	cuda_12.4.0_551.61_windows.exe	a8324145bf4fdf5c09c76a7bfc670b93	3190723024	https://developer.download.nvidia.com/compute/cuda/12.4.0/local_installers/cuda_12.4.0_551.61_windows.exe
12.4.1	linux-amd64	cuda_12.4.1_550.54.15_linux.run	afc99bab1d8c6579395d851d948ca3c1	4454730420	https://developer.download.nvidia.com/compute/cuda/12.4.1/local_installers/cuda_12.4.1_550.54.15_linux.run
12.4.1	linux-arm64	cuda_12.4.1_550.54.15_linux_sbsa.run	85e5b967f0ba7bea4015cfd40e6b9cf4	3655564991	https://developer.download.nvidia.com/compute/cuda/12.4.1/local_installers/cuda_12.4.1_550.54.15_linux_sbsa.run
12.4.1	windows-amd64	cuda_12.4.1_551.78_windows.exe	35f518faeedff72d005a244b792f21f4	3194092728	https://developer.download.nvidia.com/compute/cuda/12.4.1/local_installers/cuda_12.4.1_551.78_windows.exe
12.5.0	linux-amd64	cuda_12.5.0_555.42.02_linux.run	0bf587ce20c8e74b90701be56ae2c907	4294677299	https://developer.download.nvidia.com/compute/cuda/12.5.0/local_installers/cuda_12.5.0_555.42.02_linux.run
12.5.0	linux-arm64	cuda_12.5.0_555.42.02_linux_sbsa.run	02703e55d897d48b4c699842aeac17c3	3592511372	https://developer.download.nvidia.com/compute/cuda/12.5.0/local_installers/cuda_12.5.0_555.42.02_linux_sbsa.run
12.5.0	windows-amd64	cuda_12.5.0_555.85_windows.exe	c61ee7a6b4ef1268ebf093d50df60411	3164477160	https://developer.download.nvidia.com/compute/cuda/12.5.0/local_installers/cuda_12.5.0_555.85_windows.exe
12.5.1	linux-amd64	cuda_12.5.1_555.42.06_linux.run	6521c4c2f3872e40e2c18ddf685b62bf	4311634770	https://developer.download.nvidia.com/compute/cuda/12.5.1/local_installers/cuda_12.5.1_555.42.06_linux.run
12.5.1	linux-arm64	cuda_12.5.1_555.42.06_linux_sbsa.run	a63b5ec204f90580c4e315c806c3e7a1	3609355312	https://developer.download.nvidia.com/compute/cuda/12.5.1/local_installers/cuda_12.5.1_555.42.06_linux_sbsa.run
12.5.1	windows-amd64	cuda_12.5.1_555.85_windows.exe	b0472cd446385b4791bb532932c61659	3170605432	https://developer.download.nvidia.com/compute/cuda/12.5.1/local_installers/cuda_12.5.1_555.85_windows.exe
12.6.0	linux-amd64	cuda_12.6.0_560.28.03_linux.run	8685a58497b0c7e5d964e6da7968bb1e	4333105923	https://developer.download.nvidia.com/compute/cuda/12.6.0/local_installers/cuda_12.6.0_560.28.03_linux.run
12.6.0	linux-arm64	cuda_12.6.0_560.28.03_linux_sbsa.run	db3f9bf3fccb2a814cdaf86475f49bc1	3628500696	https://developer.download.nvidia.com/compute/cuda/12.6.0/local_installers/cuda_12.6.0_560.28.03_linux_sbsa.run
12.6.0	windows-amd64	cuda_12.6.0_560.76_windows.exe	da1d96fce6997c7d9efa7d8944fb61a7	3216570184	https://developer.download.nvidia.com/compute/cuda/12.6.0/local_installers/cuda_12.6.0_560.76_windows.exe
12.6.1	linux-amd64	cuda_12.6.1_560.35.03_linux.run	a2b81a6811c9a85eccbf90f79613bc4b	4345714567	https://developer.download.nvidia.com/compute/cuda/12.6.1/local_installers/cuda_12.6.1_560.35.03_linux.run
12.6.1	linux-arm64	cuda_12.6.1_560.35.03_linux_sbsa.run	464fd4023cfcf6b10e2eb07b9973da32	3642994115	https://developer.download.nvidia.com/compute/cuda/12.6.1/local_installers/cuda_12.6.1_560.35.03_linux_sbsa.run
12.6.1	windows-amd64	cuda_12.6.1_560.94_windows.exe	63e44204efe2964b63f5888d9337bf75	3222905368	https://developer.download.nvidia.com/compute/cuda/12.6.1/local_installers/cuda_12.6.1_560.94_windows.exe
12.6.2	linux-amd64	cuda_12.6.2_560.35.03_linux.run	dcba85e2d49d7e6d93d8626f708276a4	4446677374	https://developer.download.nvidia.com/compute/cuda/12.6.2/local_installers/cuda_12.6.2_560.35.03_linux.run
12.6.2	linux-arm64	cuda_12.6.2_560.35.03_linux_sbsa.run	f629ad91a760919b8ae67187600bd6b4	3763139760	https://developer.download.nvidia.com/compute/cuda/12.6.2/local_installers/cuda_12.6.2_560.35.03_linux_sbsa.run
12.6.2	windows-amd64	cuda_12.6.2_560.94_windows.exe	05eccc6034d99da4cf80558e6a80fbdc	3241927008	https://developer.download.nvidia.com/compute/cuda/12.6.2/local_installers/cuda_12.6.2_560.94_windows.exe
12.6.3	linux-amd64	cuda_12.6.3_560.35.05_linux.run	29d297908c72b810c9ceaa5177142abd	4446722669	https://developer.download.nvidia.com/compute/cuda/12.6.3/local_installers/cuda_12.6.3_560.35.05_linux.run
12.6.3	linux-arm64	cuda_12.6.3_560.35.05_linux_sbsa.run	e1d1635339f41e46e2c45615f10520e0	3760696924	https://developer.download.nvidia.com/compute/cuda/12.6.3/local_installers/cuda_12.6.3_560.35.05_linux_sbsa.run
12.6.3	windows-amd64	cuda_12.6.3_561.17_windows.exe	57b4c58801ca5d11ba8f966f149065e3	3227678920	https://developer.download.nvidia.com/compute/cuda/12.6.3/local_installers/cuda_12.6.3_561.17_windows.exe
12.8.0	linux-amd64	cuda_12.8.0_570.86.10_linux.run	c71027cf1a4ce84f80b9cbf81116e767	5412604598	https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda_12.8.0_570.86.10_linux.run
12.8.0	linux-arm64	cuda_12.8.0_570.86.10_linux_sbsa.run	a762a64719e72f9f28211f059c848443	4636253689	https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda_12.8.0_570.86.10_linux_sbsa.run
12.8.0	windows-amd64	cuda_12.8.0_571.96_windows.exe	5e7fe0f087d65fae4c0278f13af55c1d	3383388152	https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda_12.8.0_571.96_windows.exe
12.8.1	linux-amd64	cuda_12.8.1_570.124.06_linux.run	e281945bf4ea42788db29457ebc1e6ae	5382238770	https://developer.download.nvidia.com/compute/cuda/12.8.1/local_installers/cuda_12.8.1_570.124.06_linux.run
12.8.1	linux-arm64	cuda_12.8.1_570.124.06_linux_sbsa.run	54ca506e9a2aac0ef9ca2a7640dd107d	4606631442	https://developer.download.nvidia.com/compute/cuda/12.8.1/local_installers/cuda_12.8.1_570.124.06_linux_sbsa.run
12.8.1	windows-amd64	cuda_12.8.1_572.61_windows.exe	05c59d04ac6c63686c6a0498e472c1e7	3369253408	https://developer.download.nvidia.com/compute/cuda/12.8.1/local_installers/cuda_12.8.1_572.61_windows.exe
12.8.2	linux-amd64	cuda_12.8.2_570.211.01_linux.run	82a348acc1271169ce3d2126f941f65f	5396612289	https://developer.download.nvidia.com/compute/cuda/12.8.2/local_installers/cuda_12.8.2_570.211.01_linux.run
12.8.2	linux-arm64	cuda_12.8.2_570.211.01_linux_sbsa.run	aa84e129f1b32aaefd8d65f92d65af49	4621214881	https://developer.download.nvidia.com/compute/cuda/12.8.2/local_installers/cuda_12.8.2_570.211.01_linux_sbsa.run
12.8.2	windows-amd64	cuda_12.8.2_572.61_windows.exe	2ebadb5192970fede0963c8506bcef1f	3371194432	https://developer.download.nvidia.com/compute/cuda/12.8.2/local_installers/cuda_12.8.2_572.61_windows.exe
12.9.0	linux-amd64	cuda_12.9.0_575.51.03_linux.run	a1ba6168710272c0f5eda622ca42172f	5839691742	https://developer.download.nvidia.com/compute/cuda/12.9.0/local_installers/cuda_12.9.0_575.51.03_linux.run
12.9.0	linux-arm64	cuda_12.9.0_575.51.03_linux_sbsa.run	4665821a3d703edb517026ab2ce3b47b	5058547799	https://developer.download.nvidia.com/compute/cuda/12.9.0/local_installers/cuda_12.9.0_575.51.03_linux_sbsa.run
12.9.0	windows-amd64	cuda_12.9.0_576.02_windows.exe	35cb5ce643abaf38350a0341190aacb0	3556838488	https://developer.download.nvidia.com/compute/cuda/12.9.0/local_installers/cuda_12.9.0_576.02_windows.exe
12.9.1	linux-amd64	cuda_12.9.1_575.57.08_linux.run	a52d6c204bd4268627dfdab8bfeeb0d1	5860276058	https://developer.download.nvidia.com/compute/cuda/12.9.1/local_installers/cuda_12.9.1_575.57.08_linux.run
12.9.1	linux-arm64	cuda_12.9.1_575.57.08_linux_sbsa.run	824d4aea22be39358bb809b4f738488b	5074335372	https://developer.download.nvidia.com/compute/cuda/12.9.1/local_installers/cuda_12.9.1_575.57.08_linux_sbsa.run
12.9.1	windows-amd64	cuda_12.9.1_576.57_windows.exe	9475f337896805cbe66935cbb8504d6f	3562982664	https://developer.download.nvidia.com/compute/cuda/12.9.1/local_installers/cuda_12.9.1_576.57_windows.exe
12.9.2	linux-amd64	cuda_12.9.2_575.57.08_linux.run	9b91ed7b1fb15f2ba42b94135dbc95b8	5877673931	https://developer.download.nvidia.com/compute/cuda/12.9.2/local_installers/cuda_12.9.2_575.57.08_linux.run
12.9.2	linux-arm64	cuda_12.9.2_575.57.08_linux_sbsa.run	2f42651531242294b4746f0520c21849	5091308573	https://developer.download.nvidia.com/compute/cuda/12.9.2/local_installers/cuda_12.9.2_575.57.08_linux_sbsa.run
12.9.2	windows-amd64	cuda_12.9.2_576.57_windows.exe	3507abab1105669515f3a59f3142e2f8	3562943232	https://developer.download.nvidia.com/compute/cuda/12.9.2/local_installers/cuda_12.9.2_576.57_windows.exe
13.0.0	linux-amd64	cuda_13.0.0_580.65.06_linux.run	1029f86a4565d4a814d918a6e1c7de43	4300799649	https://developer.download.nvidia.com/compute/cuda/13.0.0/local_installers/cuda_13.0.0_580.65.06_linux.run
13.0.0	linux-arm64	cuda_13.0.0_580.65.06_linux_sbsa.run	a9e6c6ef4b6452d48d980ae551dbb865	3983090672	https://developer.download.nvidia.com/compute/cuda/13.0.0/local_installers/cuda_13.0.0_580.65.06_linux_sbsa.run
13.0.0	windows-amd64	cuda_13.0.0_windows.exe	947db2f0230a3eab645a5a50856f70b9	2470810232	https://developer.download.nvidia.com/compute/cuda/13.0.0/local_installers/cuda_13.0.0_windows.exe
13.0.1	linux-amd64	cuda_13.0.1_580.82.07_linux.run	8c56e3cb1ab74370aafed5a4600bc5bc	4302469087	https://developer.download.nvidia.com/compute/cuda/13.0.1/local_installers/cuda_13.0.1_580.82.07_linux.run
13.0.1	linux-arm64	cuda_13.0.1_580.82.07_linux_sbsa.run	baf60d69c8d04d940590c4abcb17d231	4046343296	https://developer.download.nvidia.com/compute/cuda/13.0.1/local_installers/cuda_13.0.1_580.82.07_linux_sbsa.run
13.0.1	windows-amd64	cuda_13.0.1_windows.exe	c38ddba0af97d4c5fcdf6aae7c2d1714	2466022640	https://developer.download.nvidia.com/compute/cuda/13.0.1/local_installers/cuda_13.0.1_windows.exe
13.0.2	linux-amd64	cuda_13.0.2_580.95.05_linux.run	3f092554675f004250d4dfc1d6c3acc9	4328066903	https://developer.download.nvidia.com/compute/cuda/13.0.2/local_installers/cuda_13.0.2_580.95.05_linux.run
13.0.2	linux-arm64	cuda_13.0.2_580.95.05_linux_sbsa.run	89911a0ae0b2ba797025fbe1b641c4b5	4071205419	https://developer.download.nvidia.com/compute/cuda/13.0.2/local_installers/cuda_13.0.2_580.95.05_linux_sbsa.run
13.0.2	windows-amd64	cuda_13.0.2_windows.exe	ed9b5583b90bc3278a82bcdbe1a19060	2467827288	https://developer.download.nvidia.com/compute/cuda/13.0.2/local_installers/cuda_13.0.2_windows.exe
13.0.3	linux-amd64	cuda_13.0.3_580.126.20_linux.run	13acdcabcb9e939f716c29eff8c12181	4329438671	https://developer.download.nvidia.com/compute/cuda/13.0.3/local_installers/cuda_13.0.3_580.126.20_linux.run
13.0.3	linux-arm64	cuda_13.0.3_580.126.20_linux_sbsa.run	567c4b2f052a09241f06585f4ee75785	4071904076	https://developer.download.nvidia.com/compute/cuda/13.0.3/local_installers/cuda_13.0.3_580.126.20_linux_sbsa.run
13.0.3	windows-amd64	cuda_13.0.3_windows.exe	a16aceb66429b701653baeb6bea84710	2469435528	https://developer.download.nvidia.com/compute/cuda/13.0.3/local_installers/cuda_13.0.3_windows.exe
13.1.0	linux-amd64	cuda_13.1.0_590.44.01_linux.run	0ab912ad3ad00a6ea176431cb4d63318	4345889047	https://developer.download.nvidia.com/compute/cuda/13.1.0/local_installers/cuda_13.1.0_590.44.01_linux.run
13.1.0	linux-arm64	cuda_13.1.0_590.44.01_linux_sbsa.run	64eb94790c34a61199ca2bfa2cf01da6	4145516591	https://developer.download.nvidia.com/compute/cuda/13.1.0/local_installers/cuda_13.1.0_590.44.01_linux_sbsa.run
13.1.0	windows-amd64	cuda_13.1.0_windows.exe	f0225d8e6aff6724d4dfc6776b089aef	2509501632	https://developer.download.nvidia.com/compute/cuda/13.1.0/local_installers/cuda_13.1.0_windows.exe
13.1.1	linux-amd64	cuda_13.1.1_590.48.01_linux.run	8aa93a77cffa8d055db0ceb9d0e2d692	4304905023	https://developer.download.nvidia.com/compute/cuda/13.1.1/local_installers/cuda_13.1.1_590.48.01_linux.run
13.1.1	linux-arm64	cuda_13.1.1_590.48.01_linux_sbsa.run	8f488705555f1ae7df9f2e2b24056b62	4072253834	https://developer.download.nvidia.com/compute/cuda/13.1.1/local_installers/cuda_13.1.1_590.48.01_linux_sbsa.run
13.1.1	windows-amd64	cuda_13.1.1_windows.exe	fbf4e503adce456837c87079e07a98b9	2496961592	https://developer.download.nvidia.com/compute/cuda/13.1.1/local_installers/cuda_13.1.1_windows.exe
13.1.2	linux-amd64	cuda_13.1.2_590.48.01_linux.run	30335614adb11ca310c61ba1105de4fc	4304938241	https://developer.download.nvidia.com/compute/cuda/13.1.2/local_installers/cuda_13.1.2_590.48.01_linux.run
13.1.2	linux-arm64	cuda_13.1.2_590.48.01_linux_sbsa.run	a8c54459024645d289d55531b63b7ab2	4072288874	https://developer.download.nvidia.com/compute/cuda/13.1.2/local_installers/cuda_13.1.2_590.48.01_linux_sbsa.run
13.1.2	windows-amd64	cuda_13.1.2_windows.exe	d08f4b310ad919b3fe3d6da1aba869d8	2497036080	https://developer.download.nvidia.com/compute/cuda/13.1.2/local_installers/cuda_13.1.2_windows.exe
13.2.0	linux-amd64	cuda_13.2.0_595.45.04_linux.run	656f4a652313abd118fb0ae1a8b902d3	4352114739	https://developer.download.nvidia.com/compute/cuda/13.2.0/local_installers/cuda_13.2.0_595.45.04_linux.run
13.2.0	linux-arm64	cuda_13.2.0_595.45.04_linux_sbsa.run	33330a9e785d22c54fa4e17554b56f4a	4104725609	https://developer.download.nvidia.com/compute/cuda/13.2.0/local_installers/cuda_13.2.0_595.45.04_linux_sbsa.run
13.2.0	windows-amd64	cuda_13.2.0_windows.exe	58b553e346d97155d5f7476be08bed41	2475705160	https://developer.download.nvidia.com/compute/cuda/13.2.0/local_installers/cuda_13.2.0_windows.exe
13.2.1	linux-amd64	cuda_13.2.1_595.58.03_linux.run	e5b4bdf19cc27d63a8254cb486764626	4398952964	https://developer.download.nvidia.com/compute/cuda/13.2.1/local_installers/cuda_13.2.1_595.58.03_linux.run
13.2.1	linux-arm64	cuda_13.2.1_595.58.03_linux_sbsa.run	0f967b661f45691d34ccf4bd854a7291	4227789234	https://developer.download.nvidia.com/compute/cuda/13.2.1/local_installers/cuda_13.2.1_595.58.03_linux_sbsa.run
13.2.1	windows-amd64	cuda_13.2.1_windows.exe	c02fb54402b917159053ce5b8fbee2cc	2497041560	https://developer.download.nvidia.com/compute/cuda/13.2.1/local_installers/cuda_13.2.1_windows.exe
13.2.2	linux-amd64	cuda_13.2.2_595.71.05_linux.run	5d7a0bc1f719655f2083fc711cacf7cf	4400921701	https://developer.download.nvidia.com/compute/cuda/13.2.2/local_installers/cuda_13.2.2_595.71.05_linux.run
13.2.2	linux-arm64	cuda_13.2.2_595.71.05_linux_sbsa.run	de619f6c9c419863383a2065cc93fbd5	4229968117	https://developer.download.nvidia.com/compute/cuda/13.2.2/local_installers/cuda_13.2.2_595.71.05_linux_sbsa.run
13.2.2	windows-amd64	cuda_13.2.2_windows.exe	9e4bcad21f296561fa834a4eeda079d2	2499003640	https://developer.download.nvidia.com/compute/cuda/13.2.2/local_installers/cuda_13.2.2_windows.exe
13.3.0	linux-amd64	cuda_13.3.0_610.43.02_linux.run	16d68669cf659157777d2e7adaff179d	4294951215	https://developer.download.nvidia.com/compute/cuda/13.3.0/local_installers/cuda_13.3.0_610.43.02_linux.run
13.3.0	linux-arm64	cuda_13.3.0_610.43.02_linux_sbsa.run	942875ce8d841223c4d2acab4d90ac4e	4172818258	https://developer.download.nvidia.com/compute/cuda/13.3.0/local_installers/cuda_13.3.0_610.43.02_linux_sbsa.run
13.3.0	windows-amd64	cuda_13.3.0_windows.exe	74c041e24025375843b528b8484a3f17	2510583272	https://developer.download.nvidia.com/compute/cuda/13.3.0/local_installers/cuda_13.3.0_windows.exe
13.3.1	linux-amd64	cuda_13.3.1_610.43.02_linux.run	7c8d3eca60ee10d2c290bdc045f88f09	4323954814	https://developer.download.nvidia.com/compute/cuda/13.3.1/local_installers/cuda_13.3.1_610.43.02_linux.run
13.3.1	linux-arm64	cuda_13.3.1_610.43.02_linux_sbsa.run	90a4467e1ac192963b0713634d5a0e43	4202433376	https://developer.download.nvidia.com/compute/cuda/13.3.1/local_installers/cuda_13.3.1_610.43.02_linux_sbsa.run
13.3.1	windows-amd64	cuda_13.3.1_windows.exe	f5a1806cd4f1b2d140ac37e3a09d1ca8	2531940368	https://developer.download.nvidia.com/compute/cuda/13.3.1/local_installers/cuda_13.3.1_windows.exe
13.4.0	windows-amd64	cuda_13.4.0_windows_x86_64.exe	68026c390d4ab3802ea3eb37d7054649	3878929072	https://packages.nvidia.com/prerelease/cuda/13.4.0/local_installers/cuda_13.4.0_windows_x86_64.exe
13.4.0	windows-arm64	cuda_13.4.0_windows_arm64.exe	bff92fe87ba1da3656ee0fd22615eadb	3835939064	https://packages.nvidia.com/prerelease/cuda/13.4.0/local_installers/cuda_13.4.0_windows_arm64.exe
13.4.1	linux-amd64	cuda_13.4.1_linux.run	0a7ffad0f0d2f12592fe333dd3be580e	4214651928	https://developer.download.nvidia.com/compute/cuda/13.4.1/local_installers/cuda_13.4.1_linux.run
13.4.1	linux-arm64	cuda_13.4.1_linux_sbsa.run	22499618afa3a4d54021fdec2c644006	4149643845	https://developer.download.nvidia.com/compute/cuda/13.4.1/local_installers/cuda_13.4.1_linux_sbsa.run
13.4.1	windows-amd64	cuda_13.4.1_windows_x86_64.exe	76d3e0a1a99e38a8fcb5bf8aa54c8d20	3750931600	https://developer.download.nvidia.com/compute/cuda/13.4.1/local_installers/cuda_13.4.1_windows_x86_64.exe
13.4.1	windows-arm64	cuda_13.4.1_windows_arm64.exe	d5b6e3bdd4b570fdbb2c00b3a20b40bf	3711598920	https://developer.download.nvidia.com/compute/cuda/13.4.1/local_installers/cuda_13.4.1_windows_arm64.exe
CATALOG
}

normalize_platform() {
    case "$1" in
    linux-amd64 | linux-x86_64) echo linux-amd64 ;;
    linux-arm64 | linux-sbsa | linux-arm64-sbsa) echo linux-arm64 ;;
    windows-amd64 | windows-x86_64) echo windows-amd64 ;;
    windows-arm64) echo windows-arm64 ;;
    all) echo all ;;
    *) die "unknown platform: $1" ;;
    esac
}

latest_catalog() {
    catalog | awk -F '\t' '
        {
            split($1, version, ".")
            key = version[1] "." version[2]
            patch = version[3] + 0
            if (!(key in latest) || patch > latest[key])
                latest[key] = patch
            rows[key SUBSEP patch SUBSEP $2] = $0
        }
        END {
            for (row_key in rows) {
                split(row_key, parts, SUBSEP)
                if (parts[2] == latest[parts[1]])
                    print rows[row_key]
            }
        }
    ' | sort -t $'\t' -k1,1V -k2,2
}

select_catalog() {
    local version="$1"
    local platform="$2"
    catalog | awk -F '\t' -v version="$version" -v platform="$platform" '
        $1 == version && (platform == "all" || $2 == platform)
    '
}

verify_file() {
    local path="$1"
    local expected_md5="$2"
    local expected_size="$3"
    local actual_size actual_md5

    [[ -f "$path" ]] || return 1
    actual_size=$(stat -c %s -- "$path")
    [[ "$actual_size" == "$expected_size" ]] || return 1
    actual_md5=$(md5sum -- "$path")
    actual_md5=${actual_md5%% *}
    [[ "$actual_md5" == "$expected_md5" ]]
}

download_records() {
    local directory="$1"
    local count=0
    local version platform filename expected_md5 expected_size url
    local final_path part_path

    mkdir -p -- "$directory"
    while IFS=$'\t' read -r version platform filename expected_md5 expected_size url; do
        [[ -n "$version" ]] || continue
        count=$((count + 1))
        final_path="$directory/$filename"
        part_path="$final_path.part"

        if verify_file "$final_path" "$expected_md5" "$expected_size"; then
            echo "verified  $filename"
            continue
        fi
        if [[ -e "$final_path" ]]; then
            die "existing file failed verification: $final_path"
        fi
        if verify_file "$part_path" "$expected_md5" "$expected_size"; then
            mv -- "$part_path" "$final_path"
            echo "completed $filename"
            continue
        fi

        echo "download  $filename ($expected_size bytes)"
        curl --fail --location --continue-at - --retry 5 --retry-all-errors \
            --no-progress-meter --output "$part_path" "$url"
        verify_file "$part_path" "$expected_md5" "$expected_size" ||
            die "download failed verification; retained as $part_path"
        mv -- "$part_path" "$final_path"
        echo "verified  $filename"
    done

    ((count > 0)) || die "no matching installer exists"
}

preflight_space() {
    local directory="$1"
    local records_file="$2"
    local needed=0
    local available
    local version platform filename expected_md5 expected_size url
    local current_size=0

    mkdir -p -- "$directory"
    while IFS=$'\t' read -r version platform filename expected_md5 expected_size url; do
        [[ -n "$version" ]] || continue
        if [[ -f "$directory/$filename" ]]; then
            continue
        fi
        current_size=0
        if [[ -f "$directory/$filename.part" ]]; then
            current_size=$(stat -c %s -- "$directory/$filename.part")
            ((current_size > expected_size)) && current_size=0
        fi
        needed=$((needed + expected_size - current_size))
    done <"$records_file"

    available=$(df -PB1 "$directory" | awk 'NR == 2 { print $4 }')
    ((needed <= available)) ||
        die "need $needed bytes but only $available bytes are free in $directory"
}

command_list() {
    local version="${1:-}"
    local platform="${2:-}"
    [[ -z "$platform" ]] || platform=$(normalize_platform "$platform")
    printf 'VERSION\tPLATFORM\tFILENAME\tMD5\tBYTES\tURL\n'
    catalog | awk -F '\t' -v version="$version" -v platform="$platform" '
        (version == "" || $1 == version) && (platform == "" || $2 == platform)
    '
}

command_url() {
    (($# == 2)) || die "url requires VERSION and PLATFORM"
    local version="$1"
    local platform
    local records
    platform=$(normalize_platform "$2")
    [[ "$platform" != all ]] || die "url requires one concrete platform"
    records=$(select_catalog "$version" "$platform")
    [[ -n "$records" ]] || die "no matching installer exists"
    printf '%s\n' "$records" | cut -f6
}

command_download() {
    (($# >= 1 && $# <= 2)) || die "a download requires VERSION [PLATFORM]"
    local version="$1"
    local platform
    local records
    platform=$(normalize_platform "${2:-all}")
    records=$(select_catalog "$version" "$platform")
    [[ -n "$records" ]] || die "no matching installer exists"
    printf '%s\n' "$records" | download_records .
}

command_sync() {
    local purge=false
    local directory=.
    local records_file
    local version platform filename expected_md5 expected_size url
    local -A keep=()
    local path

    if [[ "${1:-}" == --purge ]]; then
        purge=true
        shift
    fi
    (($# == 0)) || die "sync accepts only the optional --purge flag"
    records_file=$(mktemp)
    latest_catalog >"$records_file"
    preflight_space "$directory" "$records_file"
    download_records "$directory" <"$records_file"

    if [[ "$purge" == true ]]; then
        while IFS=$'\t' read -r version platform filename expected_md5 expected_size url; do
            keep["$filename"]=1
        done <"$records_file"
        shopt -s nullglob
        for path in "$directory"/cuda_12*.run "$directory"/cuda_12*.exe \
            "$directory"/cuda_13*.run "$directory"/cuda_13*.exe; do
            if [[ -z "${keep[${path##*/}]:-}" ]]; then
                rm -- "$path"
                echo "removed   ${path##*/}"
            fi
        done
        shopt -u nullglob
    fi
    rm -f -- "$records_file"
}

main() {
    (($# > 0)) || {
        usage
        exit 2
    }
    local command="$1"
    shift
    case "$command" in
    list) command_list "$@" ;;
    url) command_url "$@" ;;
    sync) command_sync "$@" ;;
    -h | --help | help) usage ;;
    [0-9]*.[0-9]*.[0-9]*) command_download "$command" "$@" ;;
    *) die "unknown command or CUDA version: $command (try --help)" ;;
    esac
}

main "$@"
