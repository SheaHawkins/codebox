from util import helper
import util


class Service:
    def run(self, x):
        return self.step(x) + helper(x)

    def step(self, x):
        if x > 1:
            return x
        return 0


def main():
    return Service().run(3) + util.twice(1)
