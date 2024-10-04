# Notes on a cpp build system

Parse `include` directives in source.
There are two kinds:

- `include "foo.h"`
- `include <foo>`

The first are local to the project, the second are system headers.

For the first, check if there is a corresponding `cpp` or `cc` file.
If so create a target of the form:

```
foo.o: foo.cpp
```

For the second, check if the header file exists under `/usr/include`.
If it is under `c++`, it is a compiler header and we don't have to do anything.

If it is in, for example `/usr/include/bar/foo.h`, and `/usr/lib(64)?/bar.{a,so}` exists, then it is a third party library and we have to add a linker command of the form:

```
-lbar
```

Otherwise it is a compiler header and we do nothing.

Should probably run the preprocessor to deal with conditional includes.
Its output should be our input.
Umm... the preprocessor output is a bit odd.
Maybe think about this some more.
Don't need it for a proof of concept anyway.
