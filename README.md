# C/C++ build system

`cbs` is a simple C/C++ build system.

You give it a configuration file with the compiler and linker options to use and point it at a source file you would like to make into an executable.
`cbs` will read the `include` directives in the file recursively to determine what object files to build and link them together.
It caches intermediate build artifacts based on their content, their dependencies and the configuration used to build them.
It builds its artifacts in parallel by default.

`cbs` does not try to figure out where system or third party libraries live.
Pass the relevant `-L` and `-l` linker flags in as configuration.

## Use

Compile `source` into `binary`:

```
cbs [OPTION] source binary
```

Run `cbs --help` to see the available options.

## Configuration

`cbs` reads compiler and linker flags from a JSON configuration file of the form:

```json
{
  "cflags": [
    "-std=c11"
    , "-Werror"
  ]
  , "ldflags": [
    "-L/usr/lib64"
    , "-lsqlite"
  ]
}
```

## But why?

I never sat down to learn C or C++, and then got a job where I had to write C++, so I didn't understand how the build process for those really works.
CMake does not help with understanding that, and after writing enough Make to be dangerous I thought this wasn't as complicated as it seemed.
This project is an attempt to justify that thought.

## License

`cbs` is relased under the GPL v3.
