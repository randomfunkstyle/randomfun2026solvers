// Hot loop for fast_littleman.py.
//
// This is deliberately a narrow C ABI rather than a Python extension module:
// the Python implementation owns parsing, validation, and the public API.  It
// serializes an already-parsed immutable program plus one case into compact
// whitespace-separated integers.  The native loop returns the final validation
// state the same way.  No Node, WASM, files, or third-party C++ dependencies.

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

using i64 = std::int64_t;

struct Pos {
  int x{}, y{};
  bool operator==(const Pos& o) const { return x == o.x && y == o.y; }
};
struct PosHash {
  std::size_t operator()(const Pos& p) const {
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(p.x)) << 32) ^
           static_cast<std::uint32_t>(p.y);
  }
};
struct Dir { int x{}, y{}; };
static Pos add(Pos p, Dir d) { return {p.x + d.x, p.y + d.y}; }
static Dir cw(Dir d) { return {-d.y, d.x}; }
static Dir ccw(Dir d) { return {d.y, -d.x}; }

struct Room {
  int x1{}, y1{}, x2{}, y2{}, kind{}, sx{-1}, sy{-1};
  bool contains(Pos p) const { return x1 < p.x && p.x < x2 && y1 < p.y && p.y < y2; }
  bool border(Pos p) const {
    return x1 <= p.x && p.x <= x2 && y1 <= p.y && p.y <= y2 &&
           (p.x == x1 || p.x == x2 || p.y == y1 || p.y == y2);
  }
};
struct Pipe {
  int length{}, src{}, dst{};
  Dir dst_side{};
};
struct Runner {
  int id{}, room{};
  Pos pos{};
  Dir dir{1, 0};
  i64 a{}, b{}, bp{};
  bool halted{}, blocked{};
  // Sleeping is a pure scheduling device: a runner blocked on a pipe op is
  // parked until an event that could change the retry's outcome (see the
  // wait-list comments in Machine).  Original semantics retry every tick with
  // no side effects on failure, so extra wake-ups are harmless and missed
  // wake-ups are impossible by construction.
  bool asleep{};
};
struct LiteralKey {
  Pos p{};
  Dir d{};
  bool operator==(const LiteralKey& o) const {
    return p == o.p && d.x == o.d.x && d.y == o.d.y;
  }
};
struct LiteralHash {
  std::size_t operator()(const LiteralKey& k) const {
    return PosHash{}(k.p) ^ (static_cast<unsigned>(k.d.x + 1) << 5) ^
           static_cast<unsigned>(k.d.y + 1);
  }
};
struct Program {
  int width{}, height{}, input_room{-1}, output_room{-1};
  bool can_split{};
  std::vector<unsigned char> grid;
  std::vector<Room> rooms;
  std::vector<Pipe> pipes;
  // Pipe-op bindings resolved per cell: binding_at is a flat grid of indices
  // into binding_lists (-1 when the cell has no binding).
  std::vector<std::vector<int>> binding_lists;
  std::vector<std::int32_t> binding_at;
  std::unordered_map<LiteralKey, i64, LiteralHash> literals;
  char at(Pos p) const {
    if (p.x < 0 || p.y < 0 || p.x >= width || p.y >= height) return ' ';
    return static_cast<char>(grid[static_cast<std::size_t>(p.y) * width + p.x]);
  }
  std::size_t flat(Pos p) const {
    return static_cast<std::size_t>(p.y) * width + p.x;
  }
};

struct Case {
  std::vector<std::vector<i64>> inputs;
  bool has_expected{};
  std::vector<std::vector<i64>> expected;
  bool has_frames{};
  std::vector<std::vector<std::vector<unsigned char>>> frame_rounds;
  std::uint64_t max_ticks{};
  // Opt-in profiling.  Absent from every request that does not ask for it, so
  // the tick loop and the reply are byte-identical to the uninstrumented one.
  bool profile{};
  std::uint64_t stride{1};
  // Opt-in *opcode* attribution, a second trailing section after the first.
  // The caller tags (cell, arrival direction) pairs with a class (what the
  // runner is doing there) and an opcode (which instruction that cell belongs
  // to); the engine cuts the focus runner's timeline at `boundary` and folds
  // each segment into one opcode.  The direction matters because one cell can
  // serve two structures — a lane walked east is also somebody else's descent
  // column walked south — and only the direction tells them apart.
  bool opprof{};
  int nclass{}, nops{}, boundary{-1}, hist_pipe{-1}, value_pipe{-1};
  std::vector<std::int16_t> cell_class, cell_op;  // 4 * cells, direction-major
};

// East, south, west, north — the order the caller tags them in.
inline int dir_index(Dir d) {
  if (d.x > 0) return 0;
  if (d.y > 0) return 1;
  if (d.x < 0) return 2;
  return 3;
}

template <typename T>
bool readv(std::istringstream& in, T& v) {
  return static_cast<bool>(in >> v);
}

bool parse_request(const char* raw, Program& p, Case& c, std::string& error) {
  std::istringstream in(raw ? raw : "");
  std::string magic;
  if (!(in >> magic) || magic != "FLM1") { error = "bad native request magic"; return false; }
  if (!readv(in, p.width) || !readv(in, p.height) || p.width < 0 || p.height < 0) {
    error = "bad grid dimensions"; return false;
  }
  p.grid.resize(static_cast<std::size_t>(p.width) * p.height);
  for (auto& ch : p.grid) {
    int value;
    if (!readv(in, value)) { error = "truncated grid"; return false; }
    ch = static_cast<unsigned char>(value);
    p.can_split |= ch == 'Y';
  }
  int nrooms;
  if (!readv(in, nrooms) || nrooms < 0) { error = "bad room count"; return false; }
  p.rooms.resize(nrooms);
  for (auto& r : p.rooms) {
    if (!(in >> r.x1 >> r.y1 >> r.x2 >> r.y2 >> r.kind >> r.sx >> r.sy)) {
      error = "truncated rooms"; return false;
    }
  }
  int npipes;
  if (!readv(in, npipes) || npipes < 0) { error = "bad pipe count"; return false; }
  p.pipes.resize(npipes);
  for (auto& pipe : p.pipes) {
    if (!(in >> pipe.length >> pipe.src >> pipe.dst >> pipe.dst_side.x >> pipe.dst_side.y)) {
      error = "truncated pipes"; return false;
    }
    // The language requires length >= 2 (the parser enforces it).  The sparse
    // scheduler relies on it: front and back cells must be distinct so pipe
    // reads/writes cannot create wake events outside the shift phase.
    if (pipe.length < 2) { error = "pipe shorter than 2"; return false; }
  }
  int nbindings;
  if (!readv(in, nbindings) || nbindings < 0) { error = "bad binding count"; return false; }
  p.binding_at.assign(static_cast<std::size_t>(p.width) * p.height, -1);
  p.binding_lists.reserve(nbindings);
  for (int i = 0; i < nbindings; ++i) {
    Pos pos;
    int count;
    if (!(in >> pos.x >> pos.y >> count) || count < 0) {
      error = "bad binding"; return false;
    }
    if (pos.x < 0 || pos.y < 0 || pos.x >= p.width || pos.y >= p.height) {
      error = "binding outside grid"; return false;
    }
    std::vector<int> ids(count);
    for (int& id : ids) if (!readv(in, id)) { error = "truncated binding"; return false; }
    p.binding_at[p.flat(pos)] = static_cast<std::int32_t>(p.binding_lists.size());
    p.binding_lists.push_back(std::move(ids));
  }
  int nliterals;
  if (!readv(in, nliterals) || nliterals < 0) { error = "bad literal count"; return false; }
  for (int i = 0; i < nliterals; ++i) {
    LiteralKey key;
    i64 value;
    if (!(in >> key.p.x >> key.p.y >> key.d.x >> key.d.y >> value)) {
      error = "truncated literals"; return false;
    }
    p.literals.emplace(key, value);
  }
  if (!(in >> p.input_room >> p.output_room)) { error = "missing io rooms"; return false; }
  int rounds;
  if (!readv(in, rounds) || rounds < 0) { error = "bad input rounds"; return false; }
  c.inputs.resize(rounds);
  for (auto& round : c.inputs) {
    int count;
    if (!readv(in, count) || count < 0) { error = "bad input count"; return false; }
    round.resize(count);
    for (i64& v : round) if (!readv(in, v)) { error = "truncated input"; return false; }
  }
  int expected_flag;
  if (!readv(in, expected_flag)) { error = "missing expected flag"; return false; }
  c.has_expected = expected_flag != 0;
  if (c.has_expected) {
    if (!readv(in, rounds) || rounds < 0) { error = "bad expected rounds"; return false; }
    c.expected.resize(rounds);
    for (auto& round : c.expected) {
      int count;
      if (!readv(in, count) || count < 0) { error = "bad expected count"; return false; }
      round.resize(count);
      for (i64& v : round) if (!readv(in, v)) { error = "truncated expected"; return false; }
    }
  }
  int frame_flag;
  if (!readv(in, frame_flag)) { error = "missing frame flag"; return false; }
  c.has_frames = frame_flag != 0;
  if (c.has_frames) {
    if (!readv(in, rounds) || rounds < 0) { error = "bad frame rounds"; return false; }
    c.frame_rounds.resize(rounds);
    for (auto& round : c.frame_rounds) {
      int frame_count;
      if (!readv(in, frame_count) || frame_count < 0) { error = "bad frame count"; return false; }
      round.resize(frame_count);
      for (auto& frame : round) {
        int pixels;
        if (!readv(in, pixels) || pixels < 0) { error = "bad pixel count"; return false; }
        frame.resize(pixels);
        for (auto& pixel : frame) {
          int value;
          if (!readv(in, value) || value < 0 || value > 15) {
            error = "bad frame pixel"; return false;
          }
          pixel = static_cast<unsigned char>(value);
        }
      }
    }
  }
  if (!readv(in, c.max_ticks)) { error = "missing tick cap"; return false; }
  // Trailing and optional: a request that stops at the tick cap profiles
  // nothing, which is what every existing caller sends.
  int profile_flag = 0;
  if (readv(in, profile_flag) && profile_flag != 0) {
    c.profile = true;
    if (!readv(in, c.stride) || c.stride == 0) c.stride = 1;
    // Trailing again: a profiling request that stops here asks for the heatmap
    // only, which is what the region profiler sends.
    int op_flag = 0;
    if (readv(in, op_flag) && op_flag != 0) {
      int tagged = 0;
      if (!readv(in, c.nclass) || !readv(in, c.nops) || !readv(in, c.boundary) ||
          !readv(in, c.hist_pipe) || !readv(in, c.value_pipe) || !readv(in, tagged) ||
          c.nclass <= 0 || c.nops < 0 || tagged < 0) {
        error = "bad opcode-profile header";
        return false;
      }
      const std::size_t cells = static_cast<std::size_t>(p.width) * p.height;
      c.cell_class.assign(cells * 4, -1);
      c.cell_op.assign(cells * 4, -1);
      for (int i = 0; i < tagged; ++i) {
        long long flat = 0;
        int dir = 0, cls = 0, opc = 0;
        if (!readv(in, flat) || !readv(in, dir) || !readv(in, cls) || !readv(in, opc) ||
            flat < 0 || static_cast<std::size_t>(flat) >= cells || dir < 0 || dir > 3 ||
            cls < 0 || cls >= c.nclass || opc >= c.nops) {
          error = "bad opcode-profile cell tag";
          return false;
        }
        const std::size_t at = static_cast<std::size_t>(dir) * cells +
                               static_cast<std::size_t>(flat);
        c.cell_class[at] = static_cast<std::int16_t>(cls);
        c.cell_op[at] = static_cast<std::int16_t>(opc);
      }
      c.opprof = true;
    }
  }
  return true;
}

struct Result {
  std::vector<i64> output;
  std::uint64_t step{};
  bool halted{}, passed_known{}, passed{};
  std::string reason, fatal;
  Pos fatal_pos{-1, -1};
  // Empty unless the request asked to profile; appended after the output list
  // so a caller that did not ask cannot see a longer reply.
  std::string profile;
};

struct Display {
  int room{}, width{}, height{}, cursor{};
  std::vector<unsigned char> current, next;
  int pid[3]{-1, -1, -1};  // 0 top/ADDR, 1 left/DATA, 2 bottom/SWAP.
  // How many frames this panel has committed.  A one-display machine has one
  // of these and it is `matched_frames`; a tiled wall has one per panel, and
  // the *logical* frame is complete only when the slowest panel has committed
  // it (the panels swap on four separate pipes, so they cannot be guaranteed
  // to land on the same tick — see `lm1/display.tiled_frames_from_writes`).
  std::uint64_t commits{};
};

// Performance notes.  The tick loop is exactly the reference semantics
// (shift pipes, io, execute, displays, move) but scheduled sparsely:
//  * Pipe values never overtake one another, so a pipe is a deque of values
//    (destination-most first) plus a run-length list of the occupied cells.
//    The per-tick shift moves whole runs: every run advances one cell unless
//    it is jammed against the destination, and adjacent runs merge.  A busy
//    pipe costs O(runs), an empty pipe costs nothing.
//  * A runner blocked on a pipe op sleeps until an event that could change
//    the retry outcome.  A failed retry has no side effects, so sleeping is
//    observationally identical as long as wake-ups are never missed:
//      - r/R/U succeed when a destination (back) cell is occupied.  With all
//        pipes length >= 2, the back cell becomes occupied only in the shift
//        phase (writes go to the front cell; io fills only the input pipe's
//        front, which no runner can bind).
//      - s/S succeed when source (front) cells are free.  The front cell
//        becomes free only in the shift phase (reads take the back cell).
//    Spurious wake-ups are harmless — the reference retries every tick.
//  * Runner occupancy lives in a flat grid (only maintained when the program
//    contains Y), so execute/move need no per-tick hash maps.
class Machine {
 public:
  Machine(const Program& program, const Case& test) : p(program), c(test) {
    std::size_t npipes = p.pipes.size();
    vals.resize(npipes);
    runs.resize(npipes);
    plen.reserve(npipes);
    for (const auto& pipe : p.pipes) plen.push_back(pipe.length);
    in_nonempty.assign(npipes, 0);
    back_waiters.resize(npipes);
    front_waiters.resize(npipes);
    if (p.input_room >= 0)
      for (std::size_t i = 0; i < npipes; ++i)
        if (p.pipes[i].src == p.input_room) { in_pipe = static_cast<int>(i); break; }
    if (p.output_room >= 0)
      for (std::size_t i = 0; i < npipes; ++i)
        if (p.pipes[i].dst == p.output_room) {
          if (out_pipe < 0) out_pipe = static_cast<int>(i);
          out_pipes.push_back(static_cast<int>(i));
        }
    std::vector<std::size_t> spawn_rooms;
    for (std::size_t rid = 0; rid < p.rooms.size(); ++rid) {
      const auto& room = p.rooms[rid];
      if (room.kind == 0 && room.sx >= 0) spawn_rooms.push_back(rid);
      if (room.kind == 3) {
        int width = room.x2 - room.x1 - 1, height = room.y2 - room.y1 - 1;
        displays.push_back(
            {static_cast<int>(rid), width, height, 0,
             std::vector<unsigned char>(width * height),
             std::vector<unsigned char>(width * height),
             {display_pipe(static_cast<int>(rid), 0),
              display_pipe(static_cast<int>(rid), 1),
              display_pipe(static_cast<int>(rid), 2)}});
      }
    }
    // Initial creation order follows the @ cells in row-major order, not room
    // discovery order. A later Y split retains the parent's position here.
    std::sort(spawn_rooms.begin(), spawn_rooms.end(), [&](std::size_t a, std::size_t b) {
      const auto& lhs = p.rooms[a];
      const auto& rhs = p.rooms[b];
      return std::tie(lhs.sy, lhs.sx) < std::tie(rhs.sy, rhs.sx);
    });
    for (std::size_t rid : spawn_rooms) {
      const auto& room = p.rooms[rid];
      runners.push_back(
          {static_cast<int>(runners.size()), static_cast<int>(rid), {room.sx, room.sy}});
    }
    next_id = static_cast<int>(runners.size());
    live = static_cast<int>(runners.size());
    active.reserve(runners.size());
    for (std::size_t i = 0; i < runners.size(); ++i)
      active.push_back(static_cast<int>(i));
    dest_of.assign(runners.size(), Pos{});
    mover_stamp.assign(runners.size(), 0);
    touched_stamp.assign(runners.size(), 0);
    std::size_t cells = static_cast<std::size_t>(p.width) * p.height;
    if (p.can_split) {
      runner_at.assign(cells, -1);
      arrival_stamp.assign(cells, 0);
      arrival_first.assign(cells, -1);
      for (std::size_t i = 0; i < runners.size(); ++i)
        runner_at[p.flat(runners[i].pos)] = static_cast<std::int32_t>(i);
    }
    if (!c.has_expected && !c.has_frames) {
      for (const auto& round : c.inputs) for (i64 v : round) input.push_back(v);
    } else if (!c.inputs.empty()) {
      for (i64 v : c.inputs[0]) input.push_back(v);
    }
    if (c.has_frames) {
      for (const auto& round : c.frame_rounds) {
        for (const auto& frame : round) expected_frames.push_back(frame);
        cumulative.push_back(expected_frames.size());
      }
    } else {
      for (const auto& round : c.expected) {
        expected.insert(expected.end(), round.begin(), round.end());
        cumulative.push_back(expected.size());
      }
    }
    release_satisfied();
    if (c.profile) {
      heat.assign(cells, 0);
      heat_wait.assign(cells, 0);
      pipe_send.assign(npipes, 0);
      pipe_recv.assign(npipes, 0);
      pipe_send_blocked.assign(npipes, 0);
      pipe_recv_blocked.assign(npipes, 0);
      pipe_query.assign(npipes, 0);
      pipe_wait.assign(npipes, 0);
    }
    if (c.opprof) {
      const std::size_t ops = static_cast<std::size_t>(c.nops) + 1;  // +1 = unattributed
      op_ticks.assign(ops * c.nclass, 0);
      op_blocked.assign(ops * c.nclass, 0);
      op_exec.assign(ops, 0);
      op_pipe_ticks.assign(ops * npipes, 0);
      op_pipe_runs.assign(ops * npipes, 0);
      seg_ticks.assign(c.nclass, 0);
      seg_blocked.assign(c.nclass, 0);
      seg_pipe_ticks.assign(npipes, 0);
      seg_pipe_runs.assign(npipes, 0);
      op_hist.resize(ops);
      op_values.resize(ops);
    }
  }

  Result run() {
    if (c.profile) sample();
    if (c.opprof) sample_ops();
    while (step < c.max_ticks) {
      if (!fatal.empty()) return finish(fatal, true, false);
      if (c.has_frames && matched_frames >= expected_frames.size())
        return finish("output-settled", true, output.empty());
      if (!c.has_frames && c.has_expected && output.size() >= expected.size())
        return finish("output-settled", true, true);
      if (live == 0 && !output_in_flight()) {
        bool known = c.has_expected;
        bool pass = !known || output == expected;
        return finish("done", known, pass);
      }
      tick();
      if (c.profile && step % c.stride == 0) sample();
      // Attribution is a state machine over consecutive ticks, so it never
      // strides: it is exact or it is nothing.
      if (c.opprof) sample_ops();
    }
    return finish("tick-cap", c.has_expected, false);
  }

 private:
  const Program& p;
  const Case& c;
  std::vector<std::deque<i64>> vals;  // per pipe, destination-most value first
  std::vector<std::vector<std::pair<int, int>>> runs;  // occupied [start,end] runs, ascending, gap >= 1
  std::vector<int> plen;
  std::vector<unsigned char> in_nonempty;
  std::vector<int> nonempty;       // pipes with pcount > 0 (lazily compacted)
  std::vector<std::vector<int>> back_waiters, front_waiters;
  std::deque<Runner> runners;      // deque: stable references while spawning
  std::vector<int> active;         // non-halted, non-sleeping runners, ascending
  std::vector<int> woken, merged, movers, touched;
  std::vector<Pos> dest_of;
  std::vector<std::uint64_t> mover_stamp, touched_stamp;
  std::vector<std::int32_t> runner_at;   // live runner index per cell (Y only)
  std::vector<std::uint64_t> arrival_stamp;
  std::vector<std::int32_t> arrival_first;
  std::vector<Display> displays;
  int next_id{}, live{}, in_pipe{-1}, out_pipe{-1};
  std::vector<int> out_pipes;
  std::deque<i64> input;
  std::vector<i64> output, expected;
  std::vector<std::vector<unsigned char>> expected_frames;
  std::size_t matched_frames{};
  // The tick each *logical* frame completed on, reported back whenever frames
  // were supplied: the per-frame cost is the difference of successive entries.
  std::vector<std::uint64_t> frame_ticks;
  std::vector<std::size_t> cumulative;
  std::size_t released{};
  std::uint64_t step{};
  std::string fatal;
  Pos fatal_pos{-1, -1};

  // ── profiling (all empty unless c.profile) ─────────────────────────────────
  // heat is a per-cell count of runner-samples: a man standing still because he
  // is blocked on `r` is sampled in that cell every time, which is the point —
  // blocked time is time.  heat_wait is the sleeping-on-a-pipe subset.
  std::vector<std::uint64_t> heat, heat_wait;
  std::vector<std::uint64_t> pipe_send, pipe_recv, pipe_send_blocked, pipe_recv_blocked;
  std::vector<std::uint64_t> pipe_query, pipe_wait;
  std::uint64_t samples{};

  // ── opcode attribution (all empty unless c.opprof) ─────────────────────────
  // The focus runner is whoever stands on a tagged cell — in practice the CPU
  // man, who never leaves his room and is the only man in it.  His timeline is
  // cut at the `boundary` class (the instruction fetch), each segment is folded
  // into whichever opcode's cells it touched, and every tick of the segment is
  // charged to that opcode under the class of the cell he stood on.  Nothing is
  // sampled or extrapolated: this is every tick of the run.
  std::vector<std::uint64_t> op_ticks, op_blocked;        // (nops+1) x nclass
  std::vector<std::uint64_t> op_exec;                     // (nops+1)
  std::vector<std::uint64_t> op_pipe_ticks, op_pipe_runs;  // (nops+1) x npipes
  std::vector<std::unordered_map<std::uint64_t, std::uint64_t>> op_hist;    // blocked-run lengths
  std::vector<std::unordered_map<i64, std::uint64_t>> op_values;            // sent on value_pipe
  std::vector<std::uint64_t> seg_ticks, seg_blocked, seg_pipe_ticks, seg_pipe_runs;
  std::vector<std::uint64_t> seg_hist;
  std::unordered_map<i64, std::uint64_t> seg_values;
  std::uint64_t op_samples{}, op_outside{}, op_multi{};
  int seg_op{-1}, prev_class{-1}, run_pipe{-1};
  std::uint64_t run_len{};

  std::size_t op_slot() const {
    return seg_op < 0 ? static_cast<std::size_t>(c.nops) : static_cast<std::size_t>(seg_op);
  }

  void end_blocked_run() {
    if (run_pipe >= 0 && run_len) {
      ++seg_pipe_runs[static_cast<std::size_t>(run_pipe)];
      if (run_pipe == c.hist_pipe) seg_hist.push_back(run_len);
    }
    run_pipe = -1;
    run_len = 0;
  }

  void flush_segment() {
    std::size_t o = op_slot();
    std::uint64_t total = 0;
    for (int i = 0; i < c.nclass; ++i) total += seg_ticks[i];
    if (total) {
      ++op_exec[o];
      for (int i = 0; i < c.nclass; ++i) {
        op_ticks[o * c.nclass + i] += seg_ticks[i];
        op_blocked[o * c.nclass + i] += seg_blocked[i];
      }
      const std::size_t np = seg_pipe_ticks.size();
      for (std::size_t i = 0; i < np; ++i) {
        op_pipe_ticks[o * np + i] += seg_pipe_ticks[i];
        op_pipe_runs[o * np + i] += seg_pipe_runs[i];
      }
      for (std::uint64_t len : seg_hist) ++op_hist[o][len];
      for (const auto& kv : seg_values) op_values[o][kv.first] += kv.second;
    }
    std::fill(seg_ticks.begin(), seg_ticks.end(), 0);
    std::fill(seg_blocked.begin(), seg_blocked.end(), 0);
    std::fill(seg_pipe_ticks.begin(), seg_pipe_ticks.end(), 0);
    std::fill(seg_pipe_runs.begin(), seg_pipe_runs.end(), 0);
    seg_hist.clear();
    seg_values.clear();
    seg_op = -1;
  }

  std::size_t tag_at(const Runner& r) const {
    return static_cast<std::size_t>(dir_index(r.dir)) *
               (static_cast<std::size_t>(p.width) * p.height) +
           p.flat(r.pos);
  }

  void sample_ops() {
    ++op_samples;
    const Runner* focus = nullptr;
    int seen = 0;
    for (const Runner& r : runners) {
      if (r.halted) continue;
      if (c.cell_class[tag_at(r)] >= 0) {
        if (!focus) focus = &r;
        ++seen;
      }
    }
    if (seen > 1) ++op_multi;  // reported, never silently merged
    if (!focus) {
      ++op_outside;
      end_blocked_run();
      prev_class = -1;
      return;
    }
    const std::size_t f = p.flat(focus->pos);
    const int cls = c.cell_class[tag_at(*focus)];
    const int opc = c.cell_op[tag_at(*focus)];
    if (cls == c.boundary && prev_class != c.boundary) flush_segment();
    prev_class = cls;
    // First opcode cell wins.  Dispatch delivers the runner to his lane before
    // anything else in the segment, so the first tag is the instruction; a
    // later one can only be a structure he is falling past.
    if (opc >= 0 && seg_op < 0) seg_op = opc;
    ++seg_ticks[static_cast<std::size_t>(cls)];
    if (!focus->asleep) {
      end_blocked_run();
      return;
    }
    ++seg_blocked[static_cast<std::size_t>(cls)];
    int pid = -1;
    const std::int32_t bidx = p.binding_at[f];
    if (bidx >= 0 && !p.binding_lists[bidx].empty()) pid = p.binding_lists[bidx][0];
    if (pid != run_pipe) {
      end_blocked_run();
      run_pipe = pid;
    }
    ++run_len;
    if (pid >= 0) ++seg_pipe_ticks[static_cast<std::size_t>(pid)];
  }

  std::string encode_opprofile() {
    end_blocked_run();
    flush_segment();  // the tail segment, so nothing is dropped at the end
    std::ostringstream out;
    const std::size_t ops = op_exec.size();
    const std::size_t np = seg_pipe_ticks.size();
    out << " Q " << ops << ' ' << c.nclass << ' ' << np << ' ' << op_samples << ' '
        << op_outside << ' ' << op_multi;
    for (std::uint64_t n : op_exec) out << ' ' << n;
    for (std::uint64_t n : op_ticks) out << ' ' << n;
    for (std::uint64_t n : op_blocked) out << ' ' << n;
    std::ostringstream body;
    std::size_t rows = 0;
    for (std::size_t o = 0; o < ops; ++o)
      for (std::size_t i = 0; i < np; ++i)
        if (op_pipe_ticks[o * np + i] || op_pipe_runs[o * np + i]) {
          body << ' ' << o << ' ' << i << ' ' << op_pipe_ticks[o * np + i] << ' '
               << op_pipe_runs[o * np + i];
          ++rows;
        }
    out << ' ' << rows << body.str();
    std::ostringstream hbody;
    rows = 0;
    for (std::size_t o = 0; o < ops; ++o)
      for (const auto& kv : op_hist[o]) { hbody << ' ' << o << ' ' << kv.first << ' ' << kv.second; ++rows; }
    out << ' ' << rows << hbody.str();
    std::ostringstream vbody;
    rows = 0;
    for (std::size_t o = 0; o < ops; ++o)
      for (const auto& kv : op_values[o]) { vbody << ' ' << o << ' ' << kv.first << ' ' << kv.second; ++rows; }
    out << ' ' << rows << vbody.str();
    return out.str();
  }

  void sample() {
    ++samples;
    for (const Runner& r : runners) {
      if (r.halted) continue;
      std::size_t f = p.flat(r.pos);
      ++heat[f];
      if (!r.asleep) continue;
      ++heat_wait[f];
      // Charge the wait to whatever pipe(s) the op at his feet is bound to:
      // "which pipe is this man waiting on" is the actionable form of stalled.
      std::int32_t bidx = p.binding_at[f];
      if (bidx < 0) continue;
      for (int pid : p.binding_lists[bidx])
        if (pid >= 0) ++pipe_wait[static_cast<std::size_t>(pid)];
    }
  }

  std::string encode_profile() const {
    std::ostringstream out;
    out << " P " << samples << ' ' << c.stride << ' ' << pipe_send.size();
    for (std::size_t i = 0; i < pipe_send.size(); ++i)
      out << ' ' << pipe_send[i] << ' ' << pipe_recv[i] << ' ' << pipe_send_blocked[i]
          << ' ' << pipe_recv_blocked[i] << ' ' << pipe_query[i] << ' ' << pipe_wait[i];
    std::size_t occupied = 0;
    for (std::uint64_t n : heat) occupied += n != 0;
    out << ' ' << occupied;
    for (std::size_t f = 0; f < heat.size(); ++f) {
      if (!heat[f]) continue;
      out << ' ' << static_cast<int>(f % p.width) << ' ' << static_cast<int>(f / p.width)
          << ' ' << heat[f] << ' ' << heat_wait[f];
    }
    return out.str();
  }

  std::string encode_frame_ticks() const {
    std::ostringstream out;
    out << " F " << frame_ticks.size();
    for (std::uint64_t t : frame_ticks) out << ' ' << t;
    return out.str();
  }

  Result finish(std::string reason, bool known, bool pass) {
    bool halted = true;
    for (const auto& r : runners) halted &= r.halted;
    std::string prof;
    if (c.profile) prof = encode_profile();
    if (c.opprof) prof += encode_opprofile();
    if (c.has_frames) prof += encode_frame_ticks();
    return {output, step, halted, known, pass, std::move(reason), fatal, fatal_pos, std::move(prof)};
  }
  bool output_in_flight() const {
    for (int pid : out_pipes) if (!vals[pid].empty()) return true;
    return false;
  }
  void die(const char* why, Pos pos) { fatal = why; fatal_pos = pos; }

  bool front_occupied(int pid) const {
    return !runs[pid].empty() && runs[pid].front().first == 0;
  }
  bool back_occupied(int pid) const {
    return !runs[pid].empty() && runs[pid].back().second == plen[pid] - 1;
  }
  void put_front(int pid, i64 v) {
    vals[pid].push_back(v);
    auto& rs = runs[pid];
    if (!rs.empty() && rs.front().first == 1) rs.front().first = 0;
    else rs.insert(rs.begin(), {0, 0});
    if (!in_nonempty[pid]) {
      in_nonempty[pid] = 1;
      nonempty.push_back(pid);
    }
  }
  i64 take_back(int pid) {
    i64 v = vals[pid].front();
    vals[pid].pop_front();
    auto& rs = runs[pid];
    auto& last = rs.back();
    if (last.first == last.second) rs.pop_back();
    else --last.second;
    return v;
  }
  void drain_waiters(std::vector<int>& waiters) {
    for (int idx : waiters) {
      Runner& r = runners[idx];
      if (r.asleep) {
        r.asleep = false;
        if (!r.halted) woken.push_back(idx);
      }
    }
    waiters.clear();
  }
  void shift_pipes() {
    for (std::size_t n = 0; n < nonempty.size();) {
      int pid = nonempty[n];
      auto& rs = runs[pid];
      if (rs.empty()) {
        in_nonempty[pid] = 0;
        nonempty[n] = nonempty.back();
        nonempty.pop_back();
        continue;
      }
      int len = plen[pid];
      bool back_was = rs.back().second == len - 1;
      bool front_was = rs.front().first == 0;
      int prev = len;  // (already shifted) start of the run ahead; wall sentinel
      for (int i = static_cast<int>(rs.size()) - 1; i >= 0; --i) {
        auto& run = rs[i];
        if (run.second + 1 < prev) { ++run.first; ++run.second; }
        if (static_cast<std::size_t>(i + 1) < rs.size() && run.second + 1 == rs[i + 1].first) {
          rs[i + 1].first = run.first;
          rs.erase(rs.begin() + i);
          prev = rs[i].first;
        } else {
          prev = run.first;
        }
      }
      if (!back_was && rs.back().second == len - 1) drain_waiters(back_waiters[pid]);
      if (front_was && rs.front().first != 0) drain_waiters(front_waiters[pid]);
      ++n;
    }
  }
  void tick() {
    ++step;
    shift_pipes();
    io();
    if (!fatal.empty()) return;
    if (!woken.empty()) {
      std::sort(woken.begin(), woken.end());
      merged.clear();
      std::merge(active.begin(), active.end(), woken.begin(), woken.end(),
                 std::back_inserter(merged));
      active.swap(merged);
      woken.clear();
    }
    execute();
    if (!fatal.empty()) return;
    execute_displays();
    if (!fatal.empty()) return;
    move();
  }
  void io() {
    if (out_pipe >= 0 && back_occupied(out_pipe)) {
      if (c.profile) ++pipe_recv[static_cast<std::size_t>(out_pipe)];
      i64 v = take_back(out_pipe);
      output.push_back(v);
      if (c.has_expected) {
        std::size_t i = output.size() - 1;
        if (i >= expected.size() || v != expected[i]) { die("wrong-output", {-1, -1}); return; }
        release_satisfied();
      }
    }
    if (in_pipe >= 0 && !input.empty() && !front_occupied(in_pipe)) {
      if (c.profile) ++pipe_send[static_cast<std::size_t>(in_pipe)];
      put_front(in_pipe, input.front());
      input.pop_front();
    }
  }
  void release_satisfied() {
    std::size_t progress = c.has_frames ? matched_frames : output.size();
    while (released < cumulative.size() && progress >= cumulative[released]) {
      ++released;
      if (released < c.inputs.size())
        for (i64 item : c.inputs[released]) input.push_back(item);
    }
  }
  int display_pipe(int room, int side) const {
    // 0 top/ADDR, 1 left/DATA, 2 bottom/SWAP.
    for (std::size_t i = 0; i < p.pipes.size(); ++i) {
      const auto& pipe = p.pipes[i];
      if (pipe.dst != room) continue;
      Dir d = pipe.dst_side;
      if ((side == 0 && d.x == 0 && d.y == 1) ||
          (side == 1 && d.x == 1 && d.y == 0) ||
          (side == 2 && d.x == 0 && d.y == -1))
        return static_cast<int>(i);
    }
    return -1;
  }
  bool take_display(int pid, i64& value) {
    if (pid < 0 || !back_occupied(pid)) return false;
    if (c.profile) ++pipe_recv[static_cast<std::size_t>(pid)];
    value = take_back(pid);
    return true;
  }
  void execute_displays() {
    for (std::size_t di = 0; di < displays.size(); ++di) {
      auto& display = displays[di];
      i64 value;
      if (take_display(display.pid[0], value)) {
        if (value < 0 || value >= display.width * display.height) {
          die("display-address", {-1, -1}); return;
        }
        display.cursor = static_cast<int>(value);
      }
      if (take_display(display.pid[1], value)) {
        if (value < 0 || value > 15) { die("display-color", {-1, -1}); return; }
        display.next[display.cursor] = static_cast<unsigned char>(value);
        display.cursor = (display.cursor + 1) % static_cast<int>(display.next.size());
      }
      if (take_display(display.pid[2], value)) {
        if (value != 0 && value != 1) { die("display-swap", {-1, -1}); return; }
        display.current = display.next;
        if (value == 0) {
          std::fill(display.next.begin(), display.next.end(), 0);
          display.cursor = 0;
        }
        if (c.has_frames) {
          // The expected frame is the whole wall: `displays.size()` tiles laid
          // out in display (reading) order, each `tile` pixels.  A one-display
          // machine is the degenerate case and compares the whole thing.
          const std::size_t tile = display.next.size();
          const std::size_t k = display.commits;
          if (k >= expected_frames.size()) { die("wrong-frame", {-1, -1}); return; }
          const auto& want = expected_frames[k];
          if (want.size() != tile * displays.size()) {
            die("frame-shape", {-1, -1}); return;
          }
          if (!std::equal(display.current.begin(), display.current.end(),
                          want.begin() + static_cast<std::ptrdiff_t>(di * tile))) {
            die("wrong-frame", {-1, -1}); return;
          }
          ++display.commits;
          std::uint64_t slowest = display.commits;
          for (const auto& other : displays) slowest = std::min(slowest, other.commits);
          if (slowest > matched_frames) {
            matched_frames = slowest;
            frame_ticks.push_back(step);
            release_satisfied();
          }
        }
      }
    }
  }
  static i64 wrap_add(i64 a, i64 b) {
    return static_cast<i64>(static_cast<std::uint64_t>(a) + static_cast<std::uint64_t>(b));
  }
  static i64 wrap_sub(i64 a, i64 b) {
    return static_cast<i64>(static_cast<std::uint64_t>(a) - static_cast<std::uint64_t>(b));
  }
  static i64 wrap_mul(i64 a, i64 b) {
    return static_cast<i64>(static_cast<std::uint64_t>(a) * static_cast<std::uint64_t>(b));
  }
  void sleep_on_backs(Runner& r, int idx, const std::vector<int>& ids) {
    r.blocked = true;
    r.asleep = true;
    for (int pid : ids) back_waiters[pid].push_back(idx);
  }
  void sleep_on_fronts(Runner& r, int idx, const std::vector<int>& ids) {
    r.blocked = true;
    r.asleep = true;
    for (int pid : ids) front_waiters[pid].push_back(idx);
  }
  void pipe_op(Runner& r, int idx, char op) {
    std::int32_t bidx = p.binding_at[p.flat(r.pos)];
    const std::vector<int>* ids = bidx < 0 ? nullptr : &p.binding_lists[bidx];
    if (!ids || ids->empty() || (*ids)[0] < 0) { die("no-pipe", r.pos); return; }
    if (op == 'q') {
      if (c.profile) ++pipe_query[static_cast<std::size_t>((*ids)[0])];
      r.bp = static_cast<i64>(vals[(*ids)[0]].size());
      return;
    }
    if (op == 's') {
      int pid = (*ids)[0];
      if (front_occupied(pid)) {
        if (c.profile) ++pipe_send_blocked[static_cast<std::size_t>(pid)];
        sleep_on_fronts(r, idx, *ids);
      } else {
        if (c.profile) ++pipe_send[static_cast<std::size_t>(pid)];
        if (c.opprof && pid == c.value_pipe && c.cell_class[tag_at(r)] >= 0) ++seg_values[r.a];
        put_front(pid, r.a);
      }
      return;
    }
    if (op == 'S') {
      for (int pid : *ids) if (front_occupied(pid)) {
        if (c.profile) for (int q : *ids) ++pipe_send_blocked[static_cast<std::size_t>(q)];
        sleep_on_fronts(r, idx, *ids);
        return;
      }
      for (int pid : *ids) {
        if (c.profile) ++pipe_send[static_cast<std::size_t>(pid)];
        put_front(pid, r.a);
      }
      return;
    }
    int pid = -1;
    if (op == 'r') pid = (*ids)[0];
    else for (int candidate : *ids) if (back_occupied(candidate)) { pid = candidate; break; }
    if (pid < 0 || !back_occupied(pid)) {
      if (c.profile) for (int q : *ids) if (q >= 0) ++pipe_recv_blocked[static_cast<std::size_t>(q)];
      sleep_on_backs(r, idx, *ids);
      return;
    }
    if (c.profile) ++pipe_recv[static_cast<std::size_t>(pid)];
    r.a = take_back(pid);
    if (op == 'U') r.dir = p.pipes[pid].dst_side;
  }
  void cell_clear(Pos pos) {
    if (p.can_split) runner_at[p.flat(pos)] = -1;
  }
  void birth(int child_idx) {
    Runner& child = runners[child_idx];
    const auto& room = p.rooms[child.room];
    if (room.border(child.pos) || !room.contains(child.pos)) {
      die("wall", child.pos); return;
    }
    std::int32_t& cell = runner_at[p.flat(child.pos)];
    if (cell >= 0 && !runners[cell].halted) {
      runners[cell].halted = true;
      child.halted = true;
      cell = -1;
      live -= 2;
    } else {
      cell = child_idx;
    }
  }
  void execute() {
    std::size_t n0 = active.size();
    std::size_t keep = 0;
    for (std::size_t k = 0; k < n0; ++k) {
      int idx = active[k];
      Runner& r = runners[idx];
      if (r.halted) continue;
      r.blocked = false;
      char op = p.at(r.pos);
      if (op == '@') op = ' ';
      if (op >= '0' && op <= '9') r.a = op - '0';
      else switch (op) {
        case ' ': case '.': break;
        case '`': {
          auto it = p.literals.find({r.pos, r.dir});
          if (it != p.literals.end()) r.a = it->second;
          break;
        }
        case 'M': r.b = r.a; break;
        case 'W': std::swap(r.a, r.b); break;
        case 'N': r.a = wrap_sub(0, r.a); break;
        case '+': r.a = wrap_add(r.a, r.b); break;
        case '-': r.a = wrap_sub(r.a, r.b); break;
        case '*': r.a = wrap_mul(r.a, r.b); break;
        case '%':
          if (r.b == 0) r.a = 0;
          else {
            i64 rem = r.a % r.b;
            if (rem && ((rem < 0) != (r.b < 0))) rem = wrap_add(rem, r.b);
            r.a = rem;
          }
          break;
        case '/': {
          i64 dividend = r.a, divisor = r.b;
          if (divisor == 0) { r.a = 0; r.b = dividend; }
          else if (dividend == INT64_MIN && divisor == -1) { r.a = INT64_MIN; r.b = 0; }
          else {
            i64 q = dividend / divisor, rem = dividend % divisor;
            if (rem && ((rem < 0) != (divisor < 0))) { --q; rem += divisor; }
            r.a = q; r.b = rem;
          }
          break;
        }
        case '&': r.a &= r.b; break;
        case '|': r.a |= r.b; break;
        case '~': r.a ^= r.b; break;
        case '{': r.a = (r.b < 0 || r.b > 63) ? 0 : static_cast<i64>(static_cast<std::uint64_t>(r.a) << r.b); break;
        case '}': r.a = r.b < 0 ? 0 : (r.b > 63 ? (r.a < 0 ? -1 : 0) : (r.a >> r.b)); break;
        case '>': r.dir = {1, 0}; break;
        case '<': r.dir = {-1, 0}; break;
        case '^': r.dir = {0, -1}; break;
        case 'v': case 'V': r.dir = {0, 1}; break;
        case 'X': if (r.a > 0) r.dir = cw(r.dir); else if (r.a < 0) r.dir = ccw(r.dir); break;
        case 'b': r.bp = r.a; break;
        case 'm': r.bp = wrap_sub(r.bp, 1); break;
        case 'd': if (r.bp > 0) r.dir = cw(r.dir); break;
        case 'a': if (r.bp > 0) r.dir = ccw(r.dir); break;
        case ']': r.bp >>= 1; break;
        case 'x': r.dir = (r.bp & 1) ? cw(r.dir) : ccw(r.dir); break;
        case 'Y': {
          Dir old = r.dir; Pos origin = r.pos;
          cell_clear(origin);
          r.dir = cw(old); r.pos = add(origin, r.dir); r.blocked = true;
          Dir nd = ccw(old);
          int child_idx = static_cast<int>(runners.size());
          runners.push_back(
              {next_id++, r.room, add(origin, nd), nd, r.a, r.b, r.bp, false, true});
          dest_of.emplace_back();
          mover_stamp.push_back(0);
          touched_stamp.push_back(0);
          active.push_back(child_idx);  // beyond n0: first executes next tick
          ++live;
          birth(child_idx); if (!fatal.empty()) return;
          birth(idx); if (!fatal.empty()) return;
          if (live > 65536) { die("too-many-runners", origin); return; }
          break;
        }
        case 'H': r.halted = true; cell_clear(r.pos); --live; break;
        case 's': case 'S': case 'r': case 'R': case 'U': case 'q':
          pipe_op(r, idx, op); if (!fatal.empty()) return; break;
        default: die("bad-op", r.pos); return;
      }
      if (r.halted || r.asleep) continue;
      active[keep++] = idx;
    }
    // Compact: drop halted/sleeping runners, keep this tick's spawns (they sit
    // in active[n0..] already, in ascending index order).
    if (keep < n0) {
      std::size_t tail = active.size() - n0;
      for (std::size_t t = 0; t < tail; ++t) active[keep + t] = active[n0 + t];
      active.resize(keep + tail);
    }
  }
  void move() {
    // No Y in the grid: runners can never split, so the reference engine skips
    // collision detection entirely on this path (rooms hold one runner each in
    // every checked-in solver).  Mirror that exactly.
    if (!p.can_split) {
      for (int idx : active) {
        Runner& r = runners[idx];
        if (r.halted || r.blocked) continue;
        r.pos = add(r.pos, r.dir);
        const auto& room = p.rooms[r.room];
        if (room.border(r.pos) || !room.contains(r.pos)) { die("wall", r.pos); return; }
      }
      return;
    }
    movers.clear();
    for (int idx : active) {
      Runner& r = runners[idx];
      if (r.halted || r.blocked) continue;
      dest_of[idx] = add(r.pos, r.dir);
      mover_stamp[idx] = step;
      movers.push_back(idx);
    }
    touched.clear();
    auto touch = [&](int i) {
      if (touched_stamp[i] != step) { touched_stamp[i] = step; touched.push_back(i); }
    };
    for (int idx : movers) {
      std::size_t f = p.flat(dest_of[idx]);
      if (arrival_stamp[f] == step) {
        touch(arrival_first[f]);
        touch(idx);
      } else {
        arrival_stamp[f] = step;
        arrival_first[f] = idx;
      }
      std::int32_t occupant = runner_at[f];
      if (occupant >= 0) {
        if (mover_stamp[occupant] != step) {
          touch(occupant); touch(idx);          // stationary occupant
        } else if (dest_of[occupant] == runners[idx].pos) {
          touch(occupant); touch(idx);          // head-on swap
        }
      }
    }
    for (int i : touched) {
      Runner& r = runners[i];
      r.halted = true;
      --live;
      if (mover_stamp[i] != step) runner_at[p.flat(r.pos)] = -1;
    }
    for (int idx : movers) {
      std::size_t f = p.flat(runners[idx].pos);
      if (runner_at[f] == idx) runner_at[f] = -1;
    }
    for (int idx : movers) {
      Runner& r = runners[idx];
      r.pos = dest_of[idx];
      if (r.halted) continue;
      const auto& room = p.rooms[r.room];
      if (room.border(r.pos) || !room.contains(r.pos)) { die("wall", r.pos); return; }
      runner_at[p.flat(r.pos)] = idx;
    }
  }
};

std::string encode(const Result& r) {
  std::ostringstream out;
  out << "FLMR1 " << r.step << ' ' << r.halted << ' ' << r.passed_known << ' '
      << r.passed << ' ' << r.reason << ' ' << (r.fatal.empty() ? "-" : r.fatal)
      << ' ' << r.fatal_pos.x << ' ' << r.fatal_pos.y << ' ' << r.output.size();
  for (i64 value : r.output) out << ' ' << value;
  out << r.profile;  // empty unless the request opted in
  return out.str();
}

}  // namespace

extern "C" const char* flm_run(const char* request) {
  Program program;
  Case test;
  std::string error;
  std::string result;
  if (!parse_request(request, program, test, error)) {
    result = "FLME1 " + error;
  } else {
    result = encode(Machine(program, test).run());
  }
  char* copy = static_cast<char*>(std::malloc(result.size() + 1));
  if (!copy) return nullptr;
  std::memcpy(copy, result.c_str(), result.size() + 1);
  return copy;
}

extern "C" void flm_free(const char* value) {
  std::free(const_cast<char*>(value));
}
