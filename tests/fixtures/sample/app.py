from util import helper


class Service:
    def run(self, x):
        return self.step(x) + helper(x)

    def step(self, x):
        if x > 1:
            return x
        return 0


def main():
    return Service().run(3)
