[Discrete Optimization](https://github.com/airbus/discrete-optimization/tree/master/discrete_optimization/jsp) のサンプルコードをコピー・改変して利用している。
ちなみに解いている問題(Ta68)の最適解は既知で、2784 である。

試したこと

- オリジナルの `add_int_var` を `add_int_resource_var` に変更
    - 微妙に良くなる。誤差レベル 3480 -> 3469
- formulation の変更 + 最適化
    - 良くなる。 3402
- dual bound の設定
    - 良くなる。 3291
