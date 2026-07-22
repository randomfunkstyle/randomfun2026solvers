// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SwiftSolver",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "SwiftSolver", targets: ["SwiftSolver"])
    ],
    targets: [
        .executableTarget(name: "SwiftSolver")
    ]
)
