from private_dungeons import claim_star_reward
from private_request import handler


Handler = handler("DungeonStarReward", claim_star_reward, replay=True)
