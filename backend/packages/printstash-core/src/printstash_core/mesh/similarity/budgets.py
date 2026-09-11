"""Absolute analysis ceilings; applications additionally enforce their RAM budget.

STL stores three unshared vertices per triangle, so its admission ceiling must
cover that representation before welding. These bounds never authorize a source
load by themselves: the application also checks file size and available memory.
"""

MAX_ANALYSIS_FACES = 2_000_000
MAX_ANALYSIS_VERTICES = 3 * MAX_ANALYSIS_FACES
