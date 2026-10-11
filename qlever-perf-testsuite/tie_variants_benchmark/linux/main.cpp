#include "benchmark/infrastructure/Benchmark.h"
int main() {
  for (auto& b : ad_benchmark::registry()) {
    auto results = b->runAllBenchmarks();
    results.runAll();
  }
}
