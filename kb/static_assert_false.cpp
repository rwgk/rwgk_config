template <typename T>
void foo() {
    static_assert(false, "foo<>() is not meant to be instantiated.");
}

int main() {
#ifdef USE_FOO
    foo<int>();
#endif
    return 0;
}
