import Foundation

enum SolverFailure: Error, CustomStringConvertible {
    case message(String)

    var description: String {
        switch self {
        case let .message(value):
            value
        }
    }
}

struct Arguments {
    let solver: String
    let input: String
    let output: String
}

func parseArguments(_ args: [String]) throws -> Arguments {
    var solver: String?
    var input: String?
    var output: String?
    var index = 0

    while index < args.count {
        let flag = args[index]
        switch flag {
        case "--solver", "--input", "--output":
            let valueIndex = index + 1
            guard valueIndex < args.count else {
                throw SolverFailure.message("missing value for \(flag)")
            }
            let value = args[valueIndex]
            switch flag {
            case "--solver":
                solver = value
            case "--input":
                input = value
            case "--output":
                output = value
            default:
                break
            }
            index += 2
        default:
            index += 1
        }
    }

    guard let solver else {
        throw SolverFailure.message("missing required --solver")
    }
    guard let input else {
        throw SolverFailure.message("missing required --input")
    }
    guard let output else {
        throw SolverFailure.message("missing required --output")
    }
    return Arguments(solver: solver, input: input, output: output)
}

func writeOutput(to path: String, solution: [String: Bool], meta: [String: String]) throws {
    let solutionData = try JSONSerialization.data(withJSONObject: solution, options: [.sortedKeys])
    let payload: [String: Any] = [
        "solution_b64": solutionData.base64EncodedString(),
        "meta": meta,
    ]
    var outputData = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    outputData.append(0x0A)
    let outputURL = URL(fileURLWithPath: path)
    try FileManager.default.createDirectory(
        at: outputURL.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    try outputData.write(to: outputURL)
}

do {
    let arguments = try parseArguments(Array(CommandLine.arguments.dropFirst()))
    guard arguments.solver == "swift-smoke" else {
        throw SolverFailure.message("swift solver only supports swift-smoke")
    }

    _ = try Data(contentsOf: URL(fileURLWithPath: arguments.input))
    try writeOutput(
        to: arguments.output,
        solution: ["smoke": true],
        meta: ["entrypoint": "swift-cli", "solver": "swift-smoke"]
    )
} catch let failure as SolverFailure {
    FileHandle.standardError.write(Data("\(failure.description)\n".utf8))
    exit(2)
} catch {
    FileHandle.standardError.write(Data("solver failed: \(error)\n".utf8))
    exit(1)
}
