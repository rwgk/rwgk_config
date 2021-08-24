#include <iostream>

int main() {
#if defined(__GNUG__)
  std::cout << "__GNUG__" << std::endl;
#endif
#if defined(__GNUC__)
  std::cout << "__GNUC__" << std::endl;
#endif
#if defined(__clang__)
  std::cout << "__clang__" << std::endl;
#endif
  return 0;
}
