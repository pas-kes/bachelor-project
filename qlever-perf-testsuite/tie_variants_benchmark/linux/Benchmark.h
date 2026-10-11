// Minimal replacement of QLever's benchmark infrastructure, only for running
// TieVariantsBenchmark outside of the QLever build (Linux container). It prints
// the same lines as the real infrastructure ("Running measurement ...",
// "Done in X seconds.") so the same evaluation script can be used.
#pragma once
#include <chrono>
#include <functional>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace ad_benchmark {
class ResultGroup {
 public:
  std::string name_;
  std::vector<std::pair<std::string, std::function<void()>>> measurements_;
  void addMeasurement(const std::string& name, std::function<void()> f) {
    measurements_.emplace_back(name, std::move(f));
  }
};

class BenchmarkResults {
  std::vector<std::unique_ptr<ResultGroup>> groups_;

 public:
  ResultGroup& addGroup(const std::string& name) {
    groups_.push_back(std::make_unique<ResultGroup>());
    groups_.back()->name_ = name;
    return *groups_.back();
  }
  void runAll() {
    for (auto& group : groups_) {
      for (auto& [name, function] : group->measurements_) {
        std::cerr << "INFO: Running measurement \"" << name << " of group "
                  << group->name_ << "\" ..." << std::endl;
        auto start = std::chrono::steady_clock::now();
        function();
        std::chrono::duration<double> d =
            std::chrono::steady_clock::now() - start;
        std::cerr << "INFO: Done in " << d.count() << " seconds." << std::endl;
      }
    }
  }
};

class BenchmarkInterface {
 public:
  virtual ~BenchmarkInterface() = default;
  virtual std::string name() const = 0;
  virtual BenchmarkResults runAllBenchmarks() = 0;
};

inline std::vector<std::unique_ptr<BenchmarkInterface>>& registry() {
  static std::vector<std::unique_ptr<BenchmarkInterface>> r;
  return r;
}
}  // namespace ad_benchmark

#define AD_REGISTER_BENCHMARK(Class)                                      \
  static const bool Class##Registered = [] {                              \
    ::ad_benchmark::registry().push_back(std::make_unique<Class>());      \
    return true;                                                          \
  }()
