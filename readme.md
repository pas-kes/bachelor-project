# Bachelor Project

## Initial setup
```shell
# first clone repository
git clone ad-freiburg/qlever

# install conan for dependencies 
brew install conan

# install dependencies
conan install . --build=missing

# ensure directory
mkdir -p build && cd build

# prebuild project
cmake --build . -DCMAKE_BUILD_TYPE=Release -DLOGLEVEL=INFO -DUSE_PARALLEL=true -D_NO_TIMING_TESTS=ON -DCMAKE_GTEST_DISCOVER_TESTS_DISCOVERY_MODE=PRE_TEST -DCMAKE_CXX_FLAGS="-Wno-psabi" -DCMAKE_PREFIX_PATH="/opt/homebrew/opt/icu4c;/opt/homebrew/opt/boost" -GNinja

# build cpp project
cmake --build . --target qlever-index qlever-server qlever-upgrade-index
```

## build index
```shell
qlever index --index-binary ../build/qlever-index
```

## start server
```shell
../build/qlever-server -i olympics --port 8080
```
