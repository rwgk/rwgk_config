#! /bin/bash
set -x
cat << EOT > what__cplusplus.cc
#include <iostream>
int main() { std::cout << __cplusplus << std::endl; }
EOT
g++ --version
g++ what__cplusplus.cc -std=c++11 && ./a.out
g++ what__cplusplus.cc -std=c++14 && ./a.out
g++ what__cplusplus.cc -std=c++17 && ./a.out
g++ what__cplusplus.cc -std=c++2a && ./a.out
g++ what__cplusplus.cc -std=c++20 && ./a.out
g++ what__cplusplus.cc -std=c++23 && ./a.out
g++ what__cplusplus.cc && ./a.out
