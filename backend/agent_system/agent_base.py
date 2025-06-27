class Agent:
    def __init__(self, name):
        self.name = name

    def execute(self, payload):
        raise NotImplementedError("Each agent must implement its execute method.")
