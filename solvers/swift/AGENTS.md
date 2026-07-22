# Swift Solver Instructions

This directory is a Swift Package Manager CLI package for Swift solvers.

Run Swift solvers from the repository root through the shared worker contract:

```sh
./solve --solver swift-smoke --input /tmp/input.json --output /tmp/output.json
```

You can also run the Swift package directly:

```sh
swift run --package-path solvers/swift SwiftSolver -- --solver swift-smoke --input /tmp/input.json --output /tmp/output.json
```

Build or test the Swift package from the repository root with:

```sh
swift build --package-path solvers/swift
```
