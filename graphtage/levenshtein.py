from enum import Enum
from typing import Iterator, List, Optional, Sequence, Tuple, Union

from tqdm import tqdm

from .edits import CompoundEdit, Edit, Insert, Remove
from .fibonacci import FibonacciHeap
from .search import Bounded, Range
from .tree import TreeNode
from .utils import SparseMatrix


def levenshtein_distance(s: str, t: str) -> int:
    """Canonical implementation of the Levenshtein distance metric"""
    rows = len(s) + 1
    cols = len(t) + 1
    dist: List[List[int]] = [[0] * cols for _ in range(rows)]

    for i in range(1, rows):
        dist[i][0] = i

    for i in range(1, cols):
        dist[0][i] = i

    col = row = 0
    for col in range(1, cols):
        for row in range(1, rows):
            if s[row - 1] == t[col - 1]:
                cost = 0
            else:
                cost = 1
            dist[row][col] = min(dist[row - 1][col] + 1,
                                 dist[row][col - 1] + 1,
                                 dist[row - 1][col - 1] + cost)

    return dist[row][col]


class AbstractNode(Bounded):
    def __init__(
            self,
            edit_distance: 'EditDistance',
            row: int,
            col: int,
    ):
        self.neighbors: List[SearchNode] = []
        self.edit_distance: EditDistance = edit_distance
        self.row: int = row
        self.col: int = col

    def __lt__(self, other):
        return self.bounds() < other.bounds()

    def __repr__(self):
        return f"{self.__class__.__name__}(row={self.row!r}, col={self.col!r})"


class EditType(Enum):
    MATCH = 0
    REMOVE = 1
    INSERT = 2


class FringeEdit:
    def __init__(self, from_node: 'SearchNode', to_node: 'SearchNode', edit_type: EditType):
        self.from_node: SearchNode = from_node
        self.to_node: SearchNode = to_node
        assert from_node not in to_node.neighbors
        to_node.neighbors.append(from_node)
        self.edit_type: EditType = edit_type

    def __hash__(self):
        return id(self)

    def __lt__(self, other):
        return (self.to_node.bounds() < other.to_node.bounds()) or (
                self.to_node.bounds() == other.to_node.bounds() and self.edit_type.value < other.edit_type.value
        )


class SearchNode(AbstractNode):
    def __init__(
            self,
            edit_distance: 'EditDistance',
            row: int,
            col: int,
            **kwargs
    ):
        super().__init__(edit_distance=edit_distance, row=row, col=col)
        self.node_from: TreeNode = self.edit_distance.from_seq[self.col - 1]
        self.node_to: TreeNode = self.edit_distance.to_seq[self.row - 1]
        self._fringe: List[FringeEdit] = []
        for edit_type, fringe_node in kwargs.items():
            if edit_type.upper() not in EditType.__members__.keys():
                raise ValueError(f"edit type must be one of {EditType.__members__.keys()}, not {edit_type}")
            edit = FringeEdit(
                from_node=self,
                to_node=fringe_node,
                edit_type=EditType.__members__.get(edit_type.upper())
            )
            self._fringe.append(edit)
        # changed = True
        # while changed:
        #     to_remove = set()
        #     changed = False
        #     for n1, n2 in combinations(self._fringe, 2):
        #         if n1.to_node.bounds().dominates(n2.to_node.bounds()):
        #             if n1.to_node.bounds().lower_bound == n2.to_node.bounds().upper_bound:
        #                 # there is a tie; try and save a direct match
        #                 if n2.edit_type == EditType.MATCH:
        #                     to_remove.add(n1)
        #                 else:
        #                     to_remove.add(n2)
        #             else:
        #                 to_remove.add(n2)
        #             changed = True
        #             break
        #     for node in to_remove:
        #         self._fringe.remove(node)
        #         assert self in node.to_node.neighbors
        #         node.to_node.neighbors.remove(self)
        self._match: Optional[Edit] = None
        self._bounds: Optional[Range] = None

    @property
    def match(self) -> Edit:
        if self._match is None:
            self._match = self.node_from.edits(self.node_to)
        return self._match

    def _invalidate_neighbors(self):
        for node in self.neighbors:
            node._bounds = None

    def tighten_bounds(self) -> bool:
        initial_bounds = self.bounds()
        if initial_bounds.definitive():
            return False
        while self.match.tighten_bounds():
            self._bounds = None
            if self.bounds().lower_bound > initial_bounds.lower_bound \
                    or self.bounds().upper_bound < initial_bounds.upper_bound:
                return True
        assert not self.match.valid or self.bounds().definitive()
        return False

    def best_predecessor(self) -> FringeEdit:
        return min(self._fringe)

    def ancestors(self) -> Iterator['SearchNode']:
        stack: List[SearchNode] = [f.to_node for f in self._fringe]
        result: List[SearchNode] = list(stack)
        history = set(stack)
        while stack:
            node = stack.pop()
            if isinstance(node, ConstantNode):
                continue
            fringe = (
                a for a in node._fringe if a.to_node not in history
            )
            match: Optional[SearchNode] = None
            for a in fringe:
                if a.edit_type == EditType.MATCH:
                    match = a.to_node
                else:
                    result.append(a.to_node)
                    history.add(a.to_node)
                    stack.append(a.to_node)
            if match is not None:
                result.append(match)
                history.add(match)
                stack.append(match)
        return reversed(result)

    def _bounds_fold_iterative(self):
        for node in self.ancestors():
            node.bounds()

    def bounds(self) -> Range:
        if self._bounds is None:
            bounds = self.match.bounds()
            lb, ub = bounds.lower_bound, bounds.upper_bound
            # if sum(f.to_node._bounds is not None for f in self._fringe) < len(self._fringe):
            #     # This means at least one of our fringe nodes hasn't been bounded yet.
            #     # self.bounds() is potentially recursive if our ancestors haven't been bounded yet,
            #     # which can sometimes exhaust Python's stack, so do this iteratively.
            #     self._bounds_fold_iterative()
            bounds = sorted(f.to_node.bounds() for f in self._fringe)
            assert bounds
            if len(bounds) == 1 or (
                bounds[0].dominates(bounds[1]) and (len(bounds) < 3 or bounds[0].dominates(bounds[2]))
            ):
                self._bounds = Range(lb + bounds[0].lower_bound, ub + bounds[0].upper_bound)
            else:
                lb += min(b.lower_bound for b in bounds)
                ub += max(b.upper_bound for b in bounds)
                self._bounds = Range(lb, ub)
        return self._bounds

    def __repr__(self):
        ret = f"{self.__class__.__name__}(node_from={self.node_from!r}, node_to={self.node_to!r}"
        for node in self._fringe:
            ret += f", {node.edit_type.name.lower()}={node.to_node!r}"
        return ret


class ConstantNode(AbstractNode):
    def __init__(
        self,
        edit_distance: 'EditDistance',
        row: int = 0,
        col: int = 0,
    ):
        super().__init__(
            edit_distance=edit_distance,
            row=row,
            col=col
        )
        if row == 0 and col == 0:
            cost = 0
            self.node = None
        else:
            if row == 0:
                self.node = self.edit_distance.from_seq[col-1]
            elif col == 0:
                self.node = self.edit_distance.to_seq[row-1]
            else:
                raise ValueError()
            cost = self.node.total_size + self.predecessor._cost.upper_bound
        self._cost: Range = Range(cost, cost)

    @property
    def predecessor(self) -> Optional['ConstantNode']:
        if self.row == 0 and self.col == 0:
            return None
        elif self.row == 0:
            return self.edit_distance.matrix[self.row][self.col - 1]
        elif self.col == 0:
            return self.edit_distance.matrix[self.row - 1][self.col]
        else:
            return None

    def tighten_bounds(self) -> bool:
        return False

    def bounds(self) -> Range:
        return self._cost

    def __repr__(self):
        return f"{self.__class__.__name__}(node={self.node!r}, row={self.row!r}, col={self.col!r})"


class EditDistance(CompoundEdit):
    def __init__(
            self,
            from_node: TreeNode,
            to_node: TreeNode,
            from_seq: Sequence[TreeNode],
            to_seq: Sequence[TreeNode]
    ):
        self.from_seq: Sequence[TreeNode] = from_seq
        self.to_seq: Sequence[TreeNode] = to_seq
        constant_cost = 0
        if len(from_seq) != len(to_seq):
            sizes: FibonacciHeap[TreeNode, int] = FibonacciHeap(key=lambda node: node.total_size)
            if len(from_seq) < len(to_seq):
                smaller, larger = from_seq, to_seq
            else:
                larger, smaller = from_seq, to_seq
            for node in larger:
                sizes.push(node)
            for _ in range(len(larger) - len(smaller)):
                constant_cost += sizes.pop().total_size
        cost_upper_bound = sum(node.total_size for node in from_seq) + sum(node.total_size for node in to_seq)
        self.matrix: SparseMatrix[Union[ConstantNode, SearchNode]] = SparseMatrix(
            num_rows=len(self.to_seq) + 1,
            num_cols=len(self.from_seq) + 1,
            default_value=None
        )
        self._fringe_row: int = -1
        self._fringe_col: int = 0
        self.__goal: Optional[SearchNode] = None
        super().__init__(
            from_node=from_node,
            to_node=to_node,
            constant_cost=constant_cost,
            cost_upper_bound=cost_upper_bound
        )

    def _add_node(self, row: int, col: int) -> bool:
        if self.matrix[row][col] is not None or col > len(self.from_seq) or row > len(self.to_seq):
            return False
        if row == 0 or col == 0:
            self.matrix[row][col] = ConstantNode(
                edit_distance=self,
                row=row,
                col=col
            )
            return True
        else:
            self.matrix[row][col] = SearchNode(
                edit_distance=self,
                row=row,
                col=col,
                insert=self.matrix[row - 1][col],
                remove=self.matrix[row][col - 1],
                match=self.matrix[row - 1][col - 1]
            )
            return True

    @property
    def _goal(self) -> Optional[SearchNode]:
        if self.__goal is None:
            self.__goal = self.matrix[len(self.to_seq)][len(self.from_seq)]
        return self.__goal

    def _fringe_diagonal(self) -> Iterator[Tuple[int, int]]:
        row, col = self._fringe_row, self._fringe_col
        while row >= 0 and col < self.matrix.num_cols:
            yield row, col
            row -= 1
            col += 1

    def _next_fringe(self) -> bool:
        if self._goal is not None:
            return False
        self._fringe_row += 1
        if self._fringe_row >= self.matrix.num_rows:
            self._fringe_row = self.matrix.num_rows - 1
            self._fringe_col += 1
        for row, col in self._fringe_diagonal():
            self._add_node(row, col)
        return self._fringe_col < self.matrix.num_cols - 1

    def tighten_bounds(self) -> bool:
        if self._goal is not None:
            return self._goal.tighten_bounds()
        # We are still building the matrix
        initial_bounds: Range = self.bounds()
        while True:
            # Tighten the entire fringe diagonal until every node in it is definitive
            if not self._next_fringe():
                assert self._goal is not None
                return self.tighten_bounds()
            for row, col in self._fringe_diagonal():
                while self.matrix[row][col].tighten_bounds():
                    pass
                assert self.matrix[row][col].bounds().definitive()
            if self.bounds().upper_bound < initial_bounds.upper_bound or \
                    self.bounds().lower_bound > initial_bounds.lower_bound:
                return True

    def cost(self) -> Range:
        if self._goal is not None:
            return self._goal.bounds()
        else:
            base_bounds: Range = super().cost()
            if self._fringe_row < 0:
                return base_bounds
            return Range(
                max(base_bounds.lower_bound, min(
                    self.matrix[row][col].bounds().lower_bound for row, col in self._fringe_diagonal()
                )),
                base_bounds.upper_bound
            )

    def edits(self) -> Iterator[Edit]:
        if not self.bounds().definitive():
            with tqdm(leave=False) as t:
                t.total = self.bounds().upper_bound - self.bounds().lower_bound
                while not self.bounds().definitive() and self.tighten_bounds():
                    t.update(t.total - (self.bounds().upper_bound - self.bounds().lower_bound))
        edits: List[Edit] = []
        node = self._goal
        while not isinstance(node, ConstantNode):
            best = node.best_predecessor()
            if best.edit_type == EditType.REMOVE:
                edits.append(Remove(to_remove=node.node_from, remove_from=self.from_node))
            elif best.edit_type == EditType.INSERT:
                edits.append(Insert(to_insert=node.node_to, insert_into=self.from_node))
            else:
                assert best.edit_type == EditType.MATCH
                edits.append(node.match)
            node = best.to_node
        while node.predecessor is not None:
            if node.row == 0:
                edits.append(Remove(to_remove=node.node, remove_from=self.from_node))
            else:
                edits.append(Insert(to_insert=node.node, insert_into=self.from_node))
            node = node.predecessor

        return reversed(edits)
