from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Appliance(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default='Washing machine', max_length=100)
    label: str = Field(default='', max_length=20)
    price: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    energy: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    basis: Literal['100cycles', 'cycle', 'hour', 'year'] = '100cycles'
    usage: float = Field(default=4, ge=0, le=10_000, allow_inf_nan=False)

    @model_validator(mode='after')
    def validate_usage(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('Enter a brand and model.')
        if self.basis == 'hour' and self.usage > 24:
            raise ValueError('Hours per day cannot exceed 24.')
        return self


class Comparison(BaseModel):
    appliances: list[Appliance] = Field(default_factory=list, max_length=12)
    rate: float = Field(default=.30, ge=0, le=1000, allow_inf_nan=False)
    currency: Literal['EUR', 'USD', 'GBP'] = 'EUR'
    years: Literal[5, 10] = 10
    market: str = Field(default='', max_length=100)


def calculate(comparison: Comparison):
    results = []
    for item in comparison.appliances:
        multiplier = {
            '100cycles': item.usage * 52 / 100,
            'cycle': item.usage * 52,
            'hour': item.usage * 365,
            'year': 1,
        }[item.basis]
        annual = item.energy * multiplier
        cost = annual * comparison.rate
        results.append({
            **item.model_dump(), 'annual_kwh': annual, 'annual_cost': cost,
            'five_kwh': annual * 5, 'ten_kwh': annual * 10,
            'five_total': item.price + cost * 5,
            'ten_total': item.price + cost * 10,
            'period_kwh': annual * comparison.years,
            'period_energy_cost': cost * comparison.years,
            'total': item.price + cost * comparison.years,
        })
    return results
