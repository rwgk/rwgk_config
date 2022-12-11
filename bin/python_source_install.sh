#! /usr/bin/bash
mmm="$1"  # major minor micro, e.g. "3.12.0"
rlv="$2"  # releaselevel,      e.g. "a3"
set -e
set -x
wget https://www.python.org/ftp/python/"${mmm}"/Python-"${mmm}${rlv}".tgz
tar zxvf Python-"${mmm}${rlv}".tgz
cd Python-"${mmm}${rlv}"
./configure --prefix=$HOME/usr_local_like/Python-"${mmm}${rlv}"
make -j 16 install
cd ..
rm -rf Python-"${mmm}${rlv}"
rm Python-"${mmm}${rlv}".tgz
