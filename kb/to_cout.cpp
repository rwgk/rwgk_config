#include <iostream>
inline void to_cout(const std::string &msg) { std::cout << msg << std::endl; }
// inline void to_cout(const std::string &) { }
#define LOOOKHERE(lbl) to_cout("\nLOOOK " + std::string(lbl) + " " + std::to_string(__LINE__) + " " + __FILE__)
