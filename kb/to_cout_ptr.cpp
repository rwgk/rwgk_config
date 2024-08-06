#include <string>
#ifdef PYBIND11_TO_COUT_ACTIVE
#    include <cassert>
#    include <cstddef>
#    include <cstdio>
#    include <iostream>
// See also: ptr_dehex.py
template <typename T>
std::string ptr_to_string(const T *raw_ptr) {
    char buf[3 + 16 + 1 + 100];
    buf[3 + 16] = '\0';
    std::sprintf(buf, "PTR%tx", (std::ptrdiff_t) raw_ptr);
    assert(buf[3 + 16] == '\0');
    return std::string(buf) + " " + typeid(raw_ptr).name();
}
inline void to_cout(const std::string &msg) { std::cout << msg << std::endl; }
#else
template <typename T>
std::string ptr_to_string(const T *) {
    return "";
}
inline void to_cout(const std::string &) {}
#endif
// to_cout("LOOOK " + std::to_string(__LINE__) + " " + __FILE__);
